"""R08Y Droid System — Main Orchestrator.

Wires together all modules and runs concurrent pipelines:
1. Vision pipeline (continuous): camera → face detection → tracking + recognition
2. Voice pipeline (event-driven): wake → record → STT → Claude (+ tools) → TTS,
   then a short follow-up window that needs no wake word
3. Battery pipeline (when the UPS HAT is enabled): periodic spoken warnings

Shared state allows the voice pipeline to know who the droid is looking at,
and the vision pipeline to hold tracking when the droid is speaking.
"""

import asyncio
import logging
import signal
import sys
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from r08y.config import load_config, validate_config, PowerConfig, R08YConfig
from r08y.modules.camera import CameraCapture
from r08y.modules.face_detector import FaceDetection, create_face_detector
from r08y.modules.face_recognizer import FaceRecognizer, RecognitionResult
from r08y.modules.face_tracker import FaceTracker
from r08y.modules.expression_controller import ExpressionController, InteractionState
from r08y.modules.interaction import InteractionDirector
from r08y.modules.servo_controller import create_servo_controller
from r08y.modules.voice_input import VoiceInput
from r08y.modules.speech_to_text import create_stt_provider
from r08y.modules.ai_brain import AIBrain, VisualContext
from r08y.modules.text_to_speech import TextToSpeech
from r08y.modules.sensors import create_distance_sensor, create_light_sensors
from r08y.modules.mobility import create_mobility_controller
from r08y.modules.health import collect_runtime_health
from r08y.modules.power import BatteryReading, BatteryWarnings, create_battery_monitor
from r08y.modules.robot_tools import RobotTools
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
    battery: Optional[BatteryReading] = None


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
    if not getattr(face_detector, "available", True):
        logger.warning(
            "Face detection unavailable; vision pipeline disabled: %s",
            getattr(face_detector, "detail", "detector unavailable"),
        )
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
    interaction: InteractionDirector,
    state: DroidState,
    shutdown: asyncio.Event,
    skill_manager: Optional[SkillManager] = None,
    tools: Optional[RobotTools] = None,
) -> None:
    """Event-driven voice loop: wake → record → transcribe → think → speak.

    After R08Y answers, it keeps listening for ``follow_up_seconds`` so the
    conversation can continue without the wake word, as Reachy Mini does.
    """
    if not voice_input.available:
        logger.warning("Voice input not available; voice pipeline disabled")
        await shutdown.wait()
        return
    if not voice_input.wake_available:
        logger.error(
            "Voice pipeline disabled: %s",
            voice_input.wake_detail,
        )
        await shutdown.wait()
        return

    follow_up_seconds = float(getattr(interaction, "follow_up_seconds", 0.0))
    barge_in_enabled = bool(getattr(interaction, "barge_in", False))
    logger.info(
        "Voice pipeline started; say '%s'",
        voice_input.wake_phrase,
    )

    async def set_interaction(state_name: InteractionState) -> None:
        await expression.set_interaction_state(state_name)

    async def play_until_barge_in(speech) -> bool:
        """Play speech while listening for the wake word; True if interrupted."""
        speech_task = asyncio.ensure_future(speech)
        wake_task = asyncio.ensure_future(voice_input.wait_for_wake())
        try:
            done, _ = await asyncio.wait(
                {speech_task, wake_task}, return_when=asyncio.FIRST_COMPLETED
            )
            if wake_task in done and wake_task.result():
                logger.info("Barge-in: wake word heard while speaking")
                tts.interrupt()
                await speech_task
                return True
            await speech_task
            return False
        finally:
            for task in (wake_task, speech_task):
                if not task.done():
                    task.cancel()
            await asyncio.gather(wake_task, speech_task, return_exceptions=True)

    async def speak(
        text: str,
        *,
        then: InteractionState = InteractionState.IDLE,
        gesture: bool = True,
        allow_barge_in: bool = False,
    ) -> bool:
        """Speak with body language; returns True if the listener barged in."""
        state.is_listening = False

        async def begin_body_language() -> None:
            state.is_speaking = True
            await set_interaction(InteractionState.SPEAKING)
            if gesture:
                await interaction.queue_response_gesture(text, expression)

        speech = tts.speak_and_wait(
            text,
            on_playback_start=begin_body_language,
        )
        try:
            if allow_barge_in and barge_in_enabled:
                return await play_until_barge_in(speech)
            await speech
            return False
        finally:
            state.is_speaking = False
            await set_interaction(then)

    follow_up = False
    barged_in = False
    while not shutdown.is_set():
        state.is_listening = False
        if follow_up:
            # Listen for a reply without the wake word.
            follow_up = False
            state.is_listening = True
            await set_interaction(InteractionState.LISTENING)
            audio_bytes = await voice_input.record_speech(start_timeout=follow_up_seconds)
            if audio_bytes is None:
                logger.info("Follow-up window closed; waiting for '%s'", voice_input.wake_phrase)
                continue
            is_follow_up = True
        else:
            # Wait for wake word (or go straight on after a barge-in)
            await set_interaction(InteractionState.IDLE)
            woke = True if barged_in else await voice_input.wait_for_wake()
            barged_in = False
            if not woke or shutdown.is_set():
                break

            # Interrupt any current speech
            tts.interrupt()
            await expression.cancel_motion()
            state.is_listening = True
            await set_interaction(InteractionState.LISTENING)
            await interaction.acknowledge(tts, expression)
            logger.info("Wake detected — recording speech")

            # Record user speech
            audio_bytes = await voice_input.record_speech()
            if audio_bytes is None:
                logger.warning("No speech recorded")
                state.is_listening = False
                await set_interaction(InteractionState.IDLE)
                continue
            is_follow_up = False

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
            if is_follow_up:
                # Background noise opened the window; go back to the wake word.
                await set_interaction(InteractionState.IDLE)
                continue
            await speak("I didn't catch that. Could you say it again?")
            follow_up = follow_up_seconds > 0
            continue

        logger.info("User said: '%s'", text)
        user_name = state.recognized.name if state.recognized and state.recognized.is_known else "unknown"
        follow_up = follow_up_seconds > 0

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
            command_response = await _handle_special_command(
                command, text, face_recognizer, state, camera,
            )
            if command_response:
                await speak(command_response)
            continue

        # 3. Send to AI brain
        visual = VisualContext(
            faces_visible=len(state.current_faces),
            recognized_name=state.recognized.name if state.recognized else None,
            is_known=state.recognized.is_known if state.recognized else False,
        )

        # Words Claude says before an action ("Let me look.") play while the
        # action and the next model request run.
        interim_speech: List[asyncio.Task] = []

        async def say_interim(interim_text: str) -> None:
            interim_speech.append(
                asyncio.create_task(
                    speak(interim_text, then=InteractionState.THINKING, gesture=False)
                )
            )

        try:
            response = await brain.think(
                text, visual, user_name, tools=tools, on_interim_text=say_interim
            )
        finally:
            if interim_speech:
                await asyncio.gather(*interim_speech, return_exceptions=True)

        # Let skills transform response before TTS
        if skill_manager:
            response = await skill_manager.on_voice_post_ai(response)

        if tools is not None and tools.conversation_ended:
            follow_up = False

        # Speak the response; a gesture-only answer needs no words.
        if response and response.strip():
            barged_in = await speak(
                response,
                gesture=not (tools is not None and tools.expressed),
                allow_barge_in=True,
            )
            if barged_in:
                follow_up = False
        else:
            await set_interaction(InteractionState.IDLE)


async def battery_pipeline(
    monitor,
    tts: TextToSpeech,
    state: DroidState,
    shutdown: asyncio.Event,
    config: PowerConfig,
) -> None:
    """Poll the UPS HAT and say a warning once per low-battery threshold."""
    if not monitor.available:
        await shutdown.wait()
        return
    warnings = BatteryWarnings(config)
    while not shutdown.is_set():
        reading = await monitor.read()
        if reading is not None:
            state.battery = reading
            message = warnings.evaluate(reading)
            if message:
                logger.warning("Battery: %s", reading.describe())
                await tts.speak(message)
        try:
            await asyncio.wait_for(shutdown.wait(), timeout=config.poll_seconds)
        except asyncio.TimeoutError:
            pass


async def _handle_special_command(
    command: str,
    original_text: str,
    face_recognizer: FaceRecognizer,
    state: DroidState,
    camera: Optional[CameraCapture],
) -> Optional[str]:
    """Handle special droid commands like face registration."""
    if command == "register_face":
        if not state.current_faces:
            return "I can't see anyone right now. Look at the camera and try again."
        if camera is None:
            return "Camera not available."
        frame = await camera.get_frame()
        if frame is None:
            return "I can't get a clear picture right now."
        # Extract name from text or ask
        name = _extract_name(original_text)
        if not name:
            return "What should I call you?"
        primary_face = max(state.current_faces, key=lambda d: d.area)
        success = await face_recognizer.register_face(name, frame, primary_face)
        if success:
            return f"Got it! I'll remember you as {name}."
        return "I had trouble saving your face. Try again."

    elif command == "identify":
        if state.recognized and state.recognized.is_known:
            return f"You're {state.recognized.name}!"
        elif state.current_faces:
            return "I can see you, but I don't recognize you yet. Say 'remember my face' to introduce yourself."
        return "I can't see anyone right now."

    elif command == "delete_face":
        if state.recognized and state.recognized.is_known:
            name = state.recognized.name
            await face_recognizer.delete_face(name)
            return f"I've forgotten {name}'s face."
        return "I don't recognize you, so there's nothing to forget."

    elif command == "patrol":
        return "Looking around!"
    return None


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
    battery=None,
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
        *([battery.stop()] if battery is not None else []),
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
    interaction = InteractionDirector(config.interaction)
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
    battery = create_battery_monitor(config.power)
    if config.interaction.speech_motion:
        tts.set_envelope_listener(expression.set_speech_envelope)

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
            battery.start(),
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
            face_detector=face_detector,
            battery=battery,
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
        robot_tools = RobotTools(
            config.ai,
            config.mobility,
            expression=expression,
            face_tracker=face_tracker,
            camera=camera,
            mobility=mobility,
            distance=distance,
            light_sensors=light_sensors,
            battery=battery,
            face_recognizer=face_recognizer,
            state=state,
            skill_manager=skill_manager,
        )
        if config.ai.tools_enabled:
            logger.info("Claude can use: %s", ", ".join(robot_tools.names))
        if battery.available:
            logger.info("Battery monitor: %s", battery.detail)
        elif config.power.ups_enabled:
            logger.warning("Battery monitor unavailable: %s", battery.detail)
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
            elif not voice_input.wake_available:
                await tts.speak(
                    "R08Y is online, but the Roby wake word is not armed. "
                    "Run the wake word test."
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
                interaction,
                state,
                shutdown,
                skill_manager,
                robot_tools,
            )
        )
        battery_task = asyncio.create_task(
            battery_pipeline(battery, tts, state, shutdown, config.power)
        )
        shutdown_task = asyncio.create_task(shutdown.wait())
        runtime_tasks = [vision_task, voice_task, battery_task, shutdown_task]

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
            battery=battery,
        )
