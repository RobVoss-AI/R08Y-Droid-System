"""Voice input module for R08Y.

Handles wake word detection and audio recording. Supports pvporcupine for
wake word and a simple RMS-based noise trigger as fallback.

State machine: IDLE → (wake) → LISTENING → (silence) → returns audio bytes.
"""

import asyncio
import io
import logging
import math
import struct
import wave
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from typing import Callable, Optional

from r08y.config import AudioConfig, WakeWordConfig

logger = logging.getLogger(__name__)

# Optional dependencies
try:
    import pyaudio
    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    logger.warning("PyAudio not installed — voice input disabled")

try:
    import pvporcupine
    PORCUPINE_AVAILABLE = True
except ImportError:
    PORCUPINE_AVAILABLE = False


class VoiceState(Enum):
    IDLE = auto()
    LISTENING = auto()
    PROCESSING = auto()


class VoiceInput:
    """Wake word detection and speech recording via USB microphone."""

    def __init__(self, audio_config: AudioConfig, wake_config: WakeWordConfig):
        self._audio_config = audio_config
        self._wake_config = wake_config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice")
        self._pa: Optional[object] = None
        self._stream = None
        self._porcupine = None
        self._state = VoiceState.IDLE
        self._running = False
        self.available = False

    def _init_audio(self) -> bool:
        """Initialize PyAudio and the microphone stream (runs in thread)."""
        if not PYAUDIO_AVAILABLE:
            return False
        try:
            self._pa = pyaudio.PyAudio()
            device_index = self._audio_config.device_index
            if device_index is None:
                device_index = self._find_usb_mic()

            self._stream = self._pa.open(
                format=pyaudio.paInt16,
                channels=self._audio_config.channels,
                rate=self._audio_config.sample_rate,
                input=True,
                input_device_index=device_index,
                frames_per_buffer=self._audio_config.chunk_size,
            )
            logger.info("Microphone opened (device=%s, rate=%d)", device_index, self._audio_config.sample_rate)
            return True
        except Exception:
            logger.exception("Failed to open microphone")
            return False

    def _find_usb_mic(self) -> Optional[int]:
        """Auto-detect USB microphone device index."""
        if not self._pa:
            return None
        for i in range(self._pa.get_device_count()):
            info = self._pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                name = info["name"].lower()
                if "usb" in name or "webcam" in name or "microphone" in name:
                    logger.info("Auto-detected mic: '%s' (index %d)", info["name"], i)
                    return i
        return None  # Use system default

    def _init_porcupine(self) -> bool:
        """Initialize Porcupine wake word engine (runs in thread)."""
        if not PORCUPINE_AVAILABLE:
            return False
        if not self._wake_config.porcupine_access_key or self._wake_config.porcupine_access_key.startswith("YOUR_"):
            logger.warning("Picovoice access key not set — wake word disabled, using RMS trigger")
            return False
        try:
            kwargs = {
                "access_key": self._wake_config.porcupine_access_key,
                "sensitivities": [self._wake_config.sensitivity],
            }
            if self._wake_config.keyword_path:
                kwargs["keyword_paths"] = [self._wake_config.keyword_path]
            else:
                kwargs["keywords"] = ["computer"]  # Built-in fallback keyword
            self._porcupine = pvporcupine.create(**kwargs)
            logger.info("Porcupine wake word engine initialized")
            return True
        except Exception:
            logger.exception("Failed to initialize Porcupine")
            return False

    async def start(self) -> None:
        """Initialize audio hardware and wake word engine."""
        loop = asyncio.get_event_loop()
        audio_ok = await loop.run_in_executor(self._executor, self._init_audio)
        if not audio_ok:
            return

        if self._wake_config.engine == "porcupine":
            await loop.run_in_executor(self._executor, self._init_porcupine)

        self._running = True
        self.available = True
        logger.info("Voice input ready (wake engine: %s)",
                     "porcupine" if self._porcupine else "rms")

    def _read_chunk(self) -> Optional[bytes]:
        """Read a single audio chunk from the microphone (blocking)."""
        try:
            if self._stream and self._stream.is_active():
                return self._stream.read(self._audio_config.chunk_size, exception_on_overflow=False)
        except Exception:
            logger.exception("Error reading audio chunk")
        return None

    @staticmethod
    def _compute_rms(audio_data: bytes) -> float:
        """Compute RMS energy of 16-bit PCM audio."""
        count = len(audio_data) // 2
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", audio_data)
        sum_sq = sum(s * s for s in samples)
        return math.sqrt(sum_sq / count)

    def _check_wake_porcupine(self, audio_data: bytes) -> bool:
        """Check if audio chunk contains wake word via Porcupine."""
        if not self._porcupine:
            return False
        pcm = struct.unpack_from(f"<{self._porcupine.frame_length}h", audio_data)
        keyword_index = self._porcupine.process(pcm)
        return keyword_index >= 0

    async def wait_for_wake(self) -> bool:
        """Block until wake word is detected or loud noise triggers.

        Returns True if wake event detected, False if stopped.
        """
        if not self.available:
            return False

        self._state = VoiceState.IDLE
        loop = asyncio.get_event_loop()
        logger.debug("Listening for wake word...")

        while self._running:
            chunk = await loop.run_in_executor(self._executor, self._read_chunk)
            if chunk is None:
                await asyncio.sleep(0.1)
                continue

            # Try Porcupine first
            if self._porcupine:
                if self._check_wake_porcupine(chunk):
                    logger.info("Wake word detected!")
                    self._state = VoiceState.LISTENING
                    return True
            else:
                # Fallback: RMS noise trigger
                rms = self._compute_rms(chunk)
                if rms > self._audio_config.rms_threshold:
                    logger.info("Wake trigger (RMS=%.0f)", rms)
                    self._state = VoiceState.LISTENING
                    return True

        return False

    async def record_speech(self) -> Optional[bytes]:
        """Record audio until silence is detected or max duration reached.

        Returns WAV-formatted bytes, or None on failure.
        """
        if not self.available:
            return None

        self._state = VoiceState.LISTENING
        loop = asyncio.get_event_loop()
        frames = []
        silent_chunks = 0
        max_chunks = int(
            self._audio_config.max_record_seconds
            * self._audio_config.sample_rate
            / self._audio_config.chunk_size
        )
        silence_chunks_needed = int(
            self._audio_config.silence_duration
            * self._audio_config.sample_rate
            / self._audio_config.chunk_size
        )

        logger.debug("Recording speech (max %ds)...", self._audio_config.max_record_seconds)

        for _ in range(max_chunks):
            if not self._running:
                break

            chunk = await loop.run_in_executor(self._executor, self._read_chunk)
            if chunk is None:
                continue

            frames.append(chunk)
            rms = self._compute_rms(chunk)

            if rms < self._audio_config.rms_threshold:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_needed:
                    logger.debug("Silence detected — ending recording (%d frames)", len(frames))
                    break
            else:
                silent_chunks = 0

        if not frames:
            return None

        self._state = VoiceState.PROCESSING

        # Convert to WAV bytes
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wf:
            wf.setnchannels(self._audio_config.channels)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(self._audio_config.sample_rate)
            wf.writeframes(b"".join(frames))

        wav_bytes = wav_buffer.getvalue()
        duration = len(frames) * self._audio_config.chunk_size / self._audio_config.sample_rate
        logger.info("Recorded %.1fs of speech (%d bytes)", duration, len(wav_bytes))
        return wav_bytes

    async def stop(self) -> None:
        """Stop recording and release audio resources."""
        self._running = False
        if self._stream:
            self._stream.stop_stream()
            self._stream.close()
        if self._pa:
            self._pa.terminate()
        if self._porcupine:
            self._porcupine.delete()
        self._executor.shutdown(wait=False)
        self.available = False
        logger.info("Voice input stopped")
