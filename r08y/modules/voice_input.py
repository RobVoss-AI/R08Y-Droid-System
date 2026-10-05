"""Wake-word detection and normalized capture from a named audio device."""

from __future__ import annotations

import asyncio
import io
import logging
import math
import struct
import wave
from array import array
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from typing import Optional

from r08y.config import AudioConfig, WakeWordConfig
from r08y.modules.audio_devices import find_portaudio_input

logger = logging.getLogger(__name__)

try:
    import pyaudio

    PYAUDIO_AVAILABLE = True
except ImportError:
    PYAUDIO_AVAILABLE = False
    logger.warning("PyAudio not installed; voice input disabled")

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
    """Capture the XVF3800 by name and expose processing-format PCM chunks."""

    def __init__(self, audio_config: AudioConfig, wake_config: WakeWordConfig):
        self._audio_config = audio_config
        self._wake_config = wake_config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="voice")
        self._pa: Optional[object] = None
        self._stream = None
        self._porcupine = None
        self._state = VoiceState.IDLE
        self._running = False
        self._input_rate = audio_config.sample_rate
        self._input_channels = audio_config.channels
        self._input_frames_per_buffer = audio_config.chunk_size
        self.device_name: Optional[str] = None
        self.available = False

    def _init_audio(self) -> bool:
        if not PYAUDIO_AVAILABLE:
            return False
        try:
            self._pa = pyaudio.PyAudio()
            device_index, info = find_portaudio_input(
                self._pa,
                requested_name=self._audio_config.device_name,
                requested_index=self._audio_config.device_index,
                require_match=self._audio_config.require_device_match,
            )
            self.device_name = str(info.get("name", device_index))

            attempts = [(
                self._audio_config.hardware_sample_rate or self._audio_config.sample_rate,
                self._audio_config.hardware_channels or self._audio_config.channels,
            )]
            native = (
                int(float(info.get("defaultSampleRate", self._audio_config.sample_rate))),
                min(int(info.get("maxInputChannels", 1)), self._audio_config.hardware_channels or 2),
            )
            if native not in attempts:
                attempts.append(native)

            last_error = None
            for input_rate, input_channels in attempts:
                try:
                    frames = max(
                        1,
                        round(self._audio_config.chunk_size * input_rate / self._audio_config.sample_rate),
                    )
                    self._stream = self._pa.open(
                        format=pyaudio.paInt16,
                        channels=input_channels,
                        rate=input_rate,
                        input=True,
                        input_device_index=device_index,
                        frames_per_buffer=frames,
                    )
                    self._input_rate = input_rate
                    self._input_channels = input_channels
                    self._input_frames_per_buffer = frames
                    break
                except Exception as exc:
                    last_error = exc
            if self._stream is None:
                raise RuntimeError(f"Unable to open selected microphone: {last_error}")

            logger.info(
                "Microphone opened: '%s' (index=%s, hardware=%dHz/%dch, processing=%dHz/%dch)",
                self.device_name,
                device_index,
                self._input_rate,
                self._input_channels,
                self._audio_config.sample_rate,
                self._audio_config.channels,
            )
            return True
        except Exception:
            logger.exception("Failed to open requested microphone")
            return False

    def _init_porcupine(self) -> bool:
        if not PORCUPINE_AVAILABLE:
            return False
        key = self._wake_config.porcupine_access_key
        if not key or key.startswith("YOUR_"):
            logger.warning("Picovoice key not set; using RMS wake trigger")
            return False
        try:
            kwargs = {"access_key": key, "sensitivities": [self._wake_config.sensitivity]}
            if self._wake_config.keyword_path:
                kwargs["keyword_paths"] = [self._wake_config.keyword_path]
            else:
                kwargs["keywords"] = ["computer"]
            self._porcupine = pvporcupine.create(**kwargs)
            if self._porcupine.sample_rate != self._audio_config.sample_rate:
                raise ValueError(
                    f"Porcupine requires {self._porcupine.sample_rate}Hz, "
                    f"audio processing is {self._audio_config.sample_rate}Hz"
                )
            logger.info("Porcupine wake-word engine initialized")
            return True
        except Exception:
            logger.exception("Failed to initialize Porcupine; using RMS trigger")
            if self._porcupine:
                self._porcupine.delete()
                self._porcupine = None
            return False

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        audio_ok = await loop.run_in_executor(self._executor, self._init_audio)
        if not audio_ok:
            return
        if self._wake_config.engine == "porcupine":
            await loop.run_in_executor(self._executor, self._init_porcupine)
        self._running = True
        self.available = True
        logger.info("Voice input ready (wake engine: %s)", "porcupine" if self._porcupine else "rms")

    def _normalize_audio(self, audio_data: bytes) -> bytes:
        """Downmix and resample PCM into the configured processing format."""
        if (
            self._input_rate == self._audio_config.sample_rate
            and self._input_channels == self._audio_config.channels
        ):
            return audio_data

        samples = array("h")
        samples.frombytes(audio_data)
        if self._input_channels > 1:
            mono = array("h")
            for offset in range(0, len(samples), self._input_channels):
                frame = samples[offset:offset + self._input_channels]
                if len(frame) == self._input_channels:
                    mono.append(int(sum(frame) / self._input_channels))
            samples = mono

        if self._input_rate != self._audio_config.sample_rate and samples:
            output_count = max(
                1,
                round(len(samples) * self._audio_config.sample_rate / self._input_rate),
            )
            ratio = self._input_rate / self._audio_config.sample_rate
            samples = array(
                "h",
                (samples[min(len(samples) - 1, int(index * ratio))] for index in range(output_count)),
            )

        if self._audio_config.channels > 1:
            expanded = array("h")
            for sample in samples:
                expanded.extend([sample] * self._audio_config.channels)
            samples = expanded
        return samples.tobytes()

    def _read_chunk(self) -> Optional[bytes]:
        try:
            if self._stream and self._stream.is_active():
                raw = self._stream.read(self._input_frames_per_buffer, exception_on_overflow=False)
                return self._normalize_audio(raw)
        except Exception:
            logger.exception("Error reading audio chunk")
        return None

    @staticmethod
    def _compute_rms(audio_data: bytes) -> float:
        count = len(audio_data) // 2
        if count == 0:
            return 0.0
        samples = struct.unpack(f"<{count}h", audio_data)
        return math.sqrt(sum(sample * sample for sample in samples) / count)

    def _check_wake_porcupine(self, audio_data: bytes) -> bool:
        if not self._porcupine:
            return False
        frame_bytes = self._porcupine.frame_length * 2
        if len(audio_data) < frame_bytes:
            return False
        pcm = struct.unpack_from(f"<{self._porcupine.frame_length}h", audio_data[:frame_bytes])
        return self._porcupine.process(pcm) >= 0

    async def wait_for_wake(self) -> bool:
        if not self.available:
            return False
        self._state = VoiceState.IDLE
        loop = asyncio.get_running_loop()
        while self._running:
            chunk = await loop.run_in_executor(self._executor, self._read_chunk)
            if chunk is None:
                await asyncio.sleep(0.1)
                continue
            if self._porcupine and self._check_wake_porcupine(chunk):
                self._state = VoiceState.LISTENING
                return True
            if not self._porcupine and self._compute_rms(chunk) > self._audio_config.rms_threshold:
                self._state = VoiceState.LISTENING
                return True
        return False

    async def measure_level(self, chunks: int = 5) -> Optional[float]:
        """Return average RMS for a short smoke-test sample."""
        if not self.available:
            return None
        loop = asyncio.get_running_loop()
        levels = []
        for _ in range(chunks):
            chunk = await loop.run_in_executor(self._executor, self._read_chunk)
            if chunk:
                levels.append(self._compute_rms(chunk))
        return sum(levels) / len(levels) if levels else None

    async def record_speech(self) -> Optional[bytes]:
        if not self.available:
            return None
        self._state = VoiceState.LISTENING
        loop = asyncio.get_running_loop()
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
        for _ in range(max_chunks):
            if not self._running:
                break
            chunk = await loop.run_in_executor(self._executor, self._read_chunk)
            if chunk is None:
                continue
            frames.append(chunk)
            if self._compute_rms(chunk) < self._audio_config.rms_threshold:
                silent_chunks += 1
                if silent_chunks >= silence_chunks_needed:
                    break
            else:
                silent_chunks = 0
        if not frames:
            return None

        self._state = VoiceState.PROCESSING
        wav_buffer = io.BytesIO()
        with wave.open(wav_buffer, "wb") as wav_file:
            wav_file.setnchannels(self._audio_config.channels)
            wav_file.setsampwidth(2)
            wav_file.setframerate(self._audio_config.sample_rate)
            wav_file.writeframes(b"".join(frames))
        wav_bytes = wav_buffer.getvalue()
        duration = len(frames) * self._audio_config.chunk_size / self._audio_config.sample_rate
        logger.info("Recorded %.1fs of speech (%d bytes)", duration, len(wav_bytes))
        return wav_bytes

    async def stop(self) -> None:
        self._running = False
        if self._stream:
            try:
                self._stream.stop_stream()
                self._stream.close()
            except Exception:
                logger.debug("Audio stream was already closed", exc_info=True)
        if self._pa:
            try:
                self._pa.terminate()
            except Exception:
                logger.debug("PortAudio termination failed", exc_info=True)
        if self._porcupine:
            self._porcupine.delete()
        self._executor.shutdown(wait=False)
        self.available = False
        logger.info("Voice input stopped")
