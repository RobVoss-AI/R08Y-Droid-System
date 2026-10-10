"""R08Y Droid System — Main Orchestrator.

Wires together all modules and runs two concurrent pipelines:
1. Vision pipeline (continuous): camera → face detection → tracking + recognition
2. Voice pipeline (event-driven): wake → record → STT → Claude → TTS → loop

Shared state allows the voice pipeline to know who the droid is looking at,
and the vision pipeline to hold tracking when the droid is speaking.
"""

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from r08y.config import load_config, validate_config, R08YConfig
from r08y.modules.camera import CameraCapture
from r08y.modules.face_detector import FaceDetection, create_face_detector
from r08y.modules.face_recognizer import FaceRecognizer, RecognitionResult
from r08y.modules.face_tracker import FaceTracker
from r08y.modules.expression_controller import ExpressionController, InteractionState
from r08y.modules.servo_controller import create_servo_controller
from r08y.modules.voice_input import VoiceInput
from r08y.modules.speech_to_text import create_stt_provider
from r08y.modules.ai_brain import AIBrain, VisualContext
from r08y.modules.text_to_speech import TextToSpeech
from r08y.modules.sensors import create_distance_sensor, create_light_sensors
from r08y.modules.mobility import create_mobility_controller
from r08y.modules.health import collect_runtime_health
from r08y.skills import SkillManager

logger = logging.getLogger(__name__)


@dataclass
class DroidState:
    """Shared state between vision and voice pipelines (asyncio-safe)."""
    current_faces: List[FaceDetection] = field(default_factory=list)
    recognized: Optional[RecognitionResult] = None
    is_speaking: bool = False
    is_listening: bool = False
    hardware_status: Dict[str, dict] = field(default_factory=dict)


def setup_logging(config: R08YConfig) -> None:
    """Configure logging from config."""
    handlers = [logging.StreamHandler(sys.stdout)]
    if config.logging.file:
        handlers.append(logging.FileHandler(config.logging.file))

    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
    )


async def vision_pipeline(
    camera: CameraCapture,
    face_detector,
    face_recognizer: FaceRecognizer,
    face_tracker: FaceTracker,
    state: DroidState,
    shutdown: asyncio.Event,
    skill_manager: Optional[SkillManager] = None,
) -> None:
    """Continuous vision loop: detect faces, track with servos, recognize."""
    if not camera.available:
        logger.warning("Camera not available — vision pipeline disabled")
        await shutdown.wait()
        return

    recognition_interval = 2.0  # Run expensive recognition every N seconds
    last_recognition_time = 0.0
    frame_count = 0

    logger.info("Vision pipeline started")

    while not shutdown.is_set():
        frame = await camera.get_frame()
        if frame is None:
            await asyncio.sleep(0.05)
            continue

        # Face detection (fast, every frame)
        detections = await face_detector.detect(frame)
        state.current_faces = detections

        # Dispatch to skill pipeline hooks
        if skill_manager:
            await skill_manager.on_vision_frame(frame)
            await skill_manager.on_vision_detections(frame, detections)

        # Face tracking — use the largest face
        if detections:
            primary_face = max(detections, key=lambda d: d.area)
            await face_tracker.update(primary_face)

            # Face recognition (throttled — expensive)
            now = asyncio.get_event_loop().time()
            if face_recognizer.available and now - last_recognition_time > recognition_interval:
                result = await face_recognizer.recognize(frame, primary_face)
                state.recognized = result
                last_recognition_time = now
                if result.is_known:
                    logger.debug("Recognized: %s (distance: %.2f)", result.name, result.confidence)
        else:
            state.recognized = None
            await face_tracker.update(None)  # Triggers patrol mode after timeout

        frame_count += 1
        if frame_count % 300 == 0:
            logger.debug("Vision: %d frames processed, FPS=%.1f", frame_count, camera.fps_actual)

        # Yield to event loop — don't spin at 100% CPU
        await asyncio.sleep(0.01)


async def voice_pipeline(
    voice_input: VoiceInput,
    stt,
    brain: AIBrain,
    tts: TextToSpeech,
    face_recognizer: FaceRecognizer,
    camera: CameraCapture,
    expression: ExpressionController,
    state: DroidState,
    shutdown: asyncio.Event,
    skill_manager: Optional[SkillManager] = None,
) -> None:
    """Event-driven voice loop: wake → record → transcribe → think → speak."""
    if not voice_input.available:
        logger.warning("Voice input not available — voice pipeline disabled")
        await shutdown.wait()
        return

    logger.info("Voice pipeline started — listening for wake word")

    async def set_interaction(state_name: InteractionState) -> None:
        await expression.set_interaction_state(state_name)

    async def speak(text: str) -> None:
        state.is_listening = False
        state.is_speaking = True
        await set_interaction(InteractionState.SPEAKING)
        try:
            await tts.speak_and_wait(text)
        finally:
            state.is_speaking = False
            await set_interaction(InteractionState.IDLE)

    while not shutdown.is_set():
        # Wait for wake word
        state.is_listening = False
        await set_interaction(InteractionState.IDLE)
        woke = await voice_input.wait_for_wake()
        if not woke or shutdown.is_set():
            break

        # Interrupt any current speech
        tts.interrupt()
        await expression.cancel_motion()
        state.is_listening = True
        await set_interaction(InteractionState.LISTENING)
        await expression.perform_gesture("acknowledge", wait=False, priority=5)
        logger.info("Wake detected — recording speech")

        # Record user speech
        audio_bytes = await voice_input.record_speech()
        if audio_bytes is None:
            logger.warning("No speech recorded")
            state.is_listening = False
            await set_interaction(InteractionState.IDLE)
            continue

        state.is_listening = False
        await set_interaction(InteractionState.THINKING)

        # Transcribe
        if stt is None:
            logger.warning("STT not configured — cannot transcribe")
            await set_interaction(InteractionState.IDLE)
            continue

        text = await stt.transcribe(audio_bytes)
        if not text:
            logger.warning("Empty transcription")
            await speak("I didn't catch that. Could you say it again?")
            continue

        logger.info("User said: '%s'", text)
        user_name = state.recognized.name if state.recognized and state.recognized.is_known else "unknown"

        # 1. Check skill commands first (plugin system)
        if skill_manager:
            match = skill_manager.match_command(text)
            if match:
                cmd, trigger = match
                logger.info("Skill command matched: '%s' → %s", trigger, cmd.skill_name)
                response = await cmd.handler(text, user_name)
                if response:
                    await speak(response)
                else:
                    await set_interaction(InteractionState.IDLE)
                continue

            # Let skills intercept before AI (e.g., dictation mode)
            intercepted = await skill_manager.on_voice_pre_ai(text, user_name)
            if intercepted is not None:
                await speak(intercepted)
                continue

        # 2. Check built-in special commands
        command = brain.check_special_command(text)
        if command:
            state.is_speaking = True
            await set_interaction(InteractionState.SPEAKING)
            try:
                await _handle_special_command(
                    command, text, face_recognizer, tts, state, camera,
                )
            finally:
                state.is_speaking = False
                await set_interaction(InteractionState.IDLE)
            continue

        # 3. Send to AI brain
        visual = VisualContext(
            faces_visible=len(state.current_faces),
            recognized_name=state.recognized.name if state.recognized else None,
            is_known=state.recognized.is_known if state.recognized else False,
        )

        response = await brain.think(text, visual, user_name)

        # Let skills transform response before TTS
        if skill_manager:
            response = await skill_manager.on_voice_post_ai(response)

        # Speak the response
        await speak(response)


async def _handle_special_command(
    command: str,
    original_text: str,
    face_recognizer: FaceRecognizer,
    tts: TextToSpeech,
    state: DroidState,
    camera: Optional[CameraCapture],
) -> None:
    """Handle special droid commands like face registration."""
    if command == "register_face":
        if not state.current_faces:
            await tts.speak_and_wait("I can't see anyone right now. Look at the camera and try again.")
            return
        if camera is None:
            await tts.speak_and_wait("Camera not available.")
            return
        frame = await camera.get_frame()
        if frame is None:
            await tts.speak_and_wait("I can't get a clear picture right now.")
            return
        # Extract name from text or ask
        name = _extract_name(original_text)
        if not name:
            await tts.speak_and_wait("What should I call you?")
            return  # Would need follow-up recording in full implementation
        primary_face = max(state.current_faces, key=lambda d: d.area)
        success = await face_recognizer.register_face(name, frame, primary_face)
        if success:
            await tts.speak_and_wait(f"Got it! I'll remember you as {name}.")
        else:
            await tts.speak_and_wait("I had trouble saving your face. Try again.")

    elif command == "identify":
        if state.recognized and state.recognized.is_known:
            await tts.speak_and_wait(f"You're {state.recognized.name}!")
        elif state.current_faces:
            await tts.speak_and_wait("I can see you, but I don't recognize you yet. Say 'remember my face' to introduce yourself.")
        else:
            await tts.speak_and_wait("I can't see anyone right now.")

    elif command == "delete_face":
        if state.recognized and state.recognized.is_known:
            name = state.recognized.name
            await face_recognizer.delete_face(name)
            await tts.speak_and_wait(f"I've forgotten {name}'s face.")
        else:
            await tts.speak_and_wait("I don't recognize you, so there's nothing to forget.")

    elif command == "patrol":
        await tts.speak_and_wait("Looking around!")


def _extract_name(text: str) -> Optional[str]:
    """Try to extract a name from registration phrases like 'remember my face, I'm Rob'."""
    text_lower = text.lower()
    for prefix in ["i'm ", "i am ", "my name is ", "call me "]:
        idx = text_lower.find(prefix)
        if idx >= 0:
            name = text[idx + len(prefix):].strip().rstrip(".!?,")
            if name:
                return name.title()
    return None


async def _stop_runtime(
    *,
    skill_manager,
    camera,
    servo,
    mobility,
    distance,
    light_sensors,
    face_recognizer,
    voice_input,
    brain,
    tts,
    expression=None,
) -> None:
    """Stop every component even when startup or one cleanup step fails."""
    logger.info("Shutting down...")
    try:
        await mobility.emergency_stop()
    except Exception:
        logger.exception("Emergency motor stop failed during shutdown")
    if skill_manager is not None:
        await skill_manager.stop_all()
    if expression is not None:
        try:
            await expression.stop()
        except Exception:
            logger.exception("Expression controller shutdown failed")
    results = await asyncio.gather(
        camera.stop(),
        servo.stop(),
        mobility.stop(),
        distance.stop(),
        light_sensors.stop(),
        face_recognizer.stop(),
        voice_input.stop(),
        brain.stop(),
        tts.stop(),
        return_exceptions=True,
    )
    for result in results:
        if isinstance(result, BaseException):
            logger.error(
                "Component shutdown failed",
                exc_info=(type(result), result, result.__traceback__),
            )
    logger.info("R08Y offline. Goodbye!")


async def run(config_path: str = "config.yaml") -> None:
    """Initialize, run, and always safely stop the droid."""
    config = load_config(config_path)
    config_errors = validate_config(config)
    if config_errors:
        raise ValueError("Invalid configuration: " + "; ".join(config_errors))
    setup_logging(config)
    logger.info("R08Y Droid System v0.2.0 starting up...")

    camera = CameraCapture(config.camera)
    face_detector = create_face_detector(config.face_detection)
    face_recognizer = FaceRecognizer(config.face_recognition)
    servo = create_servo_controller(config.servos)
    expression = ExpressionController(servo, config.servos, config.expression)
    face_tracker = FaceTracker(
        expression, config.servos, config.tracker, config.camera.width, config.camera.height
    )
    voice_input = VoiceInput(config.audio, config.wake_word)
    stt = create_stt_provider(config.stt)
    brain = AIBrain(config.ai)
    tts = TextToSpeech(config.tts)
    distance = create_distance_sensor(config.distance)
    light_sensors = create_light_sensors(config.light_sensors)
    mobility = create_mobility_controller(config.mobility, distance)

    state = DroidState()
    shutdown = asyncio.Event()
    skill_manager: Optional[SkillManager] = None
    runtime_tasks: List[asyncio.Task] = []

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        logger.info("Initializing modules...")
        # The upstream ADC reads params.json relative to its source directory.
        # Complete that sequentially before any other background work starts.
        await light_sensors.start()
        await asyncio.gather(
            camera.start(),
            servo.start(),
            face_recognizer.start(),
            voice_input.start(),
            brain.start(),
            tts.start(),
            distance.start(),
        )
        await expression.start()
        # The forward obstacle guard must initialize before the motors do.
        await mobility.start()

        health = await collect_runtime_health(
            config,
            camera=camera,
            servo=servo,
            voice_input=voice_input,
            tts=tts,
            distance=distance,
            light_sensors=light_sensors,
            mobility=mobility,
        )
        state.hardware_status = health.as_dict()
        logger.info("Hardware health: %s", health.summary())
        for component_name, component in health.components.items():
            if component.status == "fail":
                logger.error("Hardware check failed: %s: %s", component_name, component.detail)
            elif component.status == "warn":
                logger.warning("Hardware check warning: %s: %s", component_name, component.detail)

        import yaml
        from pathlib import Path

        raw_config = {}
        config_file = Path(config_path)
        if config_file.exists():
            with open(config_file) as config_stream:
                raw_config = yaml.safe_load(config_stream) or {}

        available_hw = set()
        for available, name in (
            (camera.available, "camera"),
            (expression.available, "servos"),
            (voice_input.available, "microphone"),
            (tts.available, "tts"),
            (tts.playback_available, "speaker"),
            (distance.available, "distance"),
            (light_sensors.available, "light_sensors"),
            (mobility.available, "mobility"),
            (health.components["network"].status == "pass", "network"),
        ):
            if available:
                available_hw.add(name)

        skill_manager = SkillManager(raw_config)
        skill_manager.load_skills(available_hw)
        await skill_manager.setup_all(
            expression,
            tts,
            camera,
            state,
            mobility=mobility,
            distance=distance,
            light_sensors=light_sensors,
        )
        await skill_manager.start_all()
        logger.info(
            "All modules initialized — R08Y is online! (%d skills loaded)",
            len(skill_manager.loaded_skills),
        )

        if tts.available:
            if voice_input.requires_usb_reseat:
                await tts.speak(
                    "R08Y is online, but microphone capture failed. "
                    "Unplug and reconnect my microphone USB cable."
                )
            else:
                await tts.speak("R08Y online. Ready when you are.")

        vision_task = asyncio.create_task(
            vision_pipeline(
                camera,
                face_detector,
                face_recognizer,
                face_tracker,
                state,
                shutdown,
                skill_manager,
            )
        )
        voice_task = asyncio.create_task(
            voice_pipeline(
                voice_input,
                stt,
                brain,
                tts,
                face_recognizer,
                camera,
                expression,
                state,
                shutdown,
                skill_manager,
            )
        )
        shutdown_task = asyncio.create_task(shutdown.wait())
        runtime_tasks = [vision_task, voice_task, shutdown_task]

        done, _ = await asyncio.wait(runtime_tasks, return_when=asyncio.FIRST_COMPLETED)
        if shutdown_task not in done:
            for task in done:
                exception = task.exception()
                if exception is not None:
                    raise exception
            raise RuntimeError("A runtime pipeline ended unexpectedly")
    finally:
        shutdown.set()
        for task in runtime_tasks:
            if not task.done():
                task.cancel()
        if runtime_tasks:
            await asyncio.gather(*runtime_tasks, return_exceptions=True)
        await _stop_runtime(
            skill_manager=skill_manager,
            camera=camera,
            servo=servo,
            mobility=mobility,
            distance=distance,
            light_sensors=light_sensors,
            face_recognizer=face_recognizer,
            voice_input=voice_input,
            brain=brain,
            tts=tts,
            expression=expression,
        )
