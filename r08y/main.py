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
from typing import List, Optional

from r08y.config import load_config, R08YConfig
from r08y.modules.camera import CameraCapture
from r08y.modules.face_detector import FaceDetection, create_face_detector
from r08y.modules.face_recognizer import FaceRecognizer, RecognitionResult
from r08y.modules.face_tracker import FaceTracker
from r08y.modules.servo_controller import create_servo_controller
from r08y.modules.voice_input import VoiceInput
from r08y.modules.speech_to_text import create_stt_provider
from r08y.modules.ai_brain import AIBrain, VisualContext
from r08y.modules.text_to_speech import TextToSpeech

logger = logging.getLogger(__name__)


@dataclass
class DroidState:
    """Shared state between vision and voice pipelines (asyncio-safe)."""
    current_faces: List[FaceDetection] = field(default_factory=list)
    recognized: Optional[RecognitionResult] = None
    is_speaking: bool = False
    is_listening: bool = False


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
) -> None:
    """Continuous vision loop: detect faces, track with servos, recognize."""
    if not camera.available:
        logger.warning("Camera not available — vision pipeline disabled")
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
    state: DroidState,
    shutdown: asyncio.Event,
) -> None:
    """Event-driven voice loop: wake → record → transcribe → think → speak."""
    if not voice_input.available:
        logger.warning("Voice input not available — voice pipeline disabled")
        return

    logger.info("Voice pipeline started — listening for wake word")

    while not shutdown.is_set():
        # Wait for wake word
        state.is_listening = False
        woke = await voice_input.wait_for_wake()
        if not woke or shutdown.is_set():
            break

        # Interrupt any current speech
        tts.interrupt()
        state.is_listening = True
        logger.info("Wake detected — recording speech")

        # Record user speech
        audio_bytes = await voice_input.record_speech()
        if audio_bytes is None:
            logger.warning("No speech recorded")
            continue

        # Transcribe
        if stt is None:
            logger.warning("STT not configured — cannot transcribe")
            continue

        text = await stt.transcribe(audio_bytes)
        if not text:
            logger.warning("Empty transcription")
            await tts.speak_and_wait("I didn't catch that. Could you say it again?")
            continue

        logger.info("User said: '%s'", text)

        # Check for special commands
        command = brain.check_special_command(text)
        if command:
            await _handle_special_command(
                command, text, face_recognizer, tts, state,
                await camera_ref_holder.get() if hasattr(camera_ref_holder, 'get') else None,
            )
            continue

        # Build visual context for Claude
        visual = VisualContext(
            faces_visible=len(state.current_faces),
            recognized_name=state.recognized.name if state.recognized else None,
            is_known=state.recognized.is_known if state.recognized else False,
        )
        user_name = state.recognized.name if state.recognized and state.recognized.is_known else "unknown"

        # Get AI response
        state.is_listening = False
        state.is_speaking = True
        response = await brain.think(text, visual, user_name)

        # Speak the response
        await tts.speak_and_wait(response)
        state.is_speaking = False


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


async def run(config_path: str = "config.yaml") -> None:
    """Main entry point: initialize all modules and run the droid."""
    config = load_config(config_path)
    setup_logging(config)

    logger.info("R08Y Droid System v0.1.0 starting up...")

    # Initialize all modules
    camera = CameraCapture(config.camera)
    face_detector = create_face_detector(config.face_detection)
    face_recognizer = FaceRecognizer(config.face_recognition)
    servo = create_servo_controller(config.servos)
    face_tracker = FaceTracker(servo, config.servos, config.tracker, config.camera.width, config.camera.height)
    voice_input = VoiceInput(config.audio, config.wake_word)
    stt = create_stt_provider(config.stt)
    brain = AIBrain(config.ai)
    tts = TextToSpeech(config.tts)

    state = DroidState()
    shutdown = asyncio.Event()

    # Graceful shutdown on SIGTERM/SIGINT
    def _signal_handler():
        logger.info("Shutdown signal received")
        shutdown.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    # Start all modules
    logger.info("Initializing modules...")
    await asyncio.gather(
        camera.start(),
        servo.start(),
        face_recognizer.start(),
        voice_input.start(),
        brain.start(),
        tts.start(),
    )

    # Store camera ref for special commands
    global camera_ref_holder
    camera_ref_holder = camera

    logger.info("All modules initialized — R08Y is online!")

    # Speak startup greeting
    if tts.available:
        await tts.speak("R08Y online. Ready when you are.")

    # Run pipelines concurrently
    try:
        await asyncio.gather(
            vision_pipeline(camera, face_detector, face_recognizer, face_tracker, state, shutdown),
            voice_pipeline(voice_input, stt, brain, tts, face_recognizer, state, shutdown),
            _shutdown_waiter(shutdown),
        )
    except Exception:
        logger.exception("Fatal error in pipeline")
    finally:
        # Graceful shutdown
        logger.info("Shutting down...")
        await asyncio.gather(
            camera.stop(),
            servo.stop(),
            face_recognizer.stop(),
            voice_input.stop(),
            brain.stop(),
            tts.stop(),
        )
        logger.info("R08Y offline. Goodbye!")


async def _shutdown_waiter(shutdown: asyncio.Event) -> None:
    """Wait for shutdown signal, then let pipelines drain."""
    await shutdown.wait()
    # Give pipelines a moment to finish current work
    await asyncio.sleep(0.5)


# Module-level reference for special commands to access camera
camera_ref_holder: Optional[CameraCapture] = None
