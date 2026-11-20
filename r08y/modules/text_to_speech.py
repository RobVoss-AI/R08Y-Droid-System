"""Text-to-speech and named-device playback for R08Y."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

from r08y.config import TTSConfig
from r08y.modules.audio_devices import PipeWireResolver
from r08y.modules.huggingface_speech import HuggingFaceSpeech, safe_exception_detail
from r08y.modules.speech_motion import speech_envelope

logger = logging.getLogger(__name__)

try:
    import edge_tts

    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts not installed; speech synthesis disabled")


ALLOWED_PLAYERS = {"mpv", "ffplay", "paplay", "pw-play", "aplay"}

EnvelopeListener = Callable[[List[float], float], Awaitable[None]]


@dataclass(frozen=True)
class _SpeechRequest:
    text: str
    on_playback_start: Optional[Callable[[], Awaitable[None]]] = None


def _synthesis_error_detail(exc: Exception, provider: str = "edge") -> str:
    """Return an actionable message without logging provider request tokens."""
    if provider == "huggingface":
        return safe_exception_detail(exc)
    status = getattr(exc, "status", None)
    if status == 403:
        return (
            "Edge TTS rejected synthesis with HTTP 403. Confirm the Pi UTC clock "
            "is synchronized; if it is correct, the upstream service may be "
            "temporarily rejecting this network."
        )
    if status is not None:
        return f"Edge TTS returned HTTP {status}."
    return f"{type(exc).__name__}: {exc}"


class TextToSpeech:
    """Queue configured speech synthesis and route it to the named sink."""

    def __init__(self, config: TTSConfig):
        self._config = config
        self._speaking = False
        self._process: Optional[asyncio.subprocess.Process] = None
        self._queue: asyncio.Queue[_SpeechRequest] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._interrupted = False
        self._last_error: Optional[str] = None
        self._play_lock = asyncio.Lock()
        self._resolved_target: Optional[str] = config.output_target
        self.output_detail = "playback path has not been checked"
        self.playback_available = False
        self.available = False
        self._huggingface: Optional[HuggingFaceSpeech] = None
        self._provider = config.provider
        self._envelope_listener: Optional[EnvelopeListener] = None

    @property
    def provider(self) -> str:
        """The provider currently speaking, after any startup fallback."""
        return self._provider

    def set_envelope_listener(self, listener: Optional[EnvelopeListener]) -> None:
        """Receive each conversational reply's loudness envelope at playback start."""
        self._envelope_listener = listener

    def _edge_fallback_ready(self) -> bool:
        return self._config.fallback_provider == "edge" and EDGE_TTS_AVAILABLE

    def _select_player(self) -> Optional[str]:
        requested = self._config.playback_command
        if requested not in ALLOWED_PLAYERS:
            logger.error("Playback command '%s' is not allowed", requested)
            return None
        if shutil.which(requested):
            return requested
        for fallback in ("pw-play", "mpv", "ffplay", "paplay", "aplay"):
            if shutil.which(fallback):
                logger.warning("Playback command '%s' not found; using %s", requested, fallback)
                self._config.playback_command = fallback
                return fallback
        return None

    async def start(self) -> None:
        player = self._select_player()
        if player is None:
            self.output_detail = "no supported audio playback command is installed"
            logger.error("No supported audio playback command is installed")
            return

        if self._config.output_device_name:
            resolver = PipeWireResolver()
            if self._config.set_pipewire_default:
                sink = await asyncio.to_thread(
                    resolver.resolve, "sink", self._config.output_device_name
                )
                if sink:
                    await asyncio.to_thread(resolver.set_default, sink)
                    self._resolved_target = sink.id
                    self.output_detail = (
                        f"{sink.name}: PipeWire sink {sink.id} selected by name via {player}"
                    )
                    logger.info("Audio output selected: '%s' (PipeWire node %s)", sink.name, sink.id)
                elif resolver.available:
                    log = logger.error if self._config.require_output_device_match else logger.warning
                    log(
                        "PipeWire sink matching '%s' was not found; using current default",
                        self._config.output_device_name,
                    )
                    if self._config.require_output_device_match:
                        self.output_detail = (
                            f"PipeWire sink matching '{self._config.output_device_name}' was not found"
                        )
                        return
                elif self._config.require_output_device_match and not self._resolved_target:
                    self.output_detail = "wpctl is required for strict named audio-output selection"
                    logger.error("wpctl is required for strict named audio-output selection")
                    return
            elif not self._resolved_target:
                sink = await asyncio.to_thread(
                    resolver.resolve, "sink", self._config.output_device_name
                )
                if sink:
                    self._resolved_target = sink.id
                    self.output_detail = (
                        f"{sink.name}: PipeWire sink {sink.id} selected by name via {player}"
                    )
                elif self._config.require_output_device_match:
                    self.output_detail = (
                        f"Audio output matching '{self._config.output_device_name}' was not found"
                    )
                    logger.error(
                        "Audio output matching '%s' was not found",
                        self._config.output_device_name,
                    )
                    return

        if self.output_detail == "playback path has not been checked":
            target = self._resolved_target or "current default"
            self.output_detail = f"{player}: output target {target}"

        self.playback_available = True
        if self._config.provider == "huggingface":
            self._huggingface = HuggingFaceSpeech(
                token=self._config.huggingface_token,
                session_url=self._config.huggingface_session_url,
                voice=self._config.huggingface_voice,
                instructions=self._config.huggingface_instructions,
                timeout_seconds=self._config.huggingface_timeout_seconds,
            )
            try:
                await self._huggingface.start()
            except Exception as exc:
                self._last_error = _synthesis_error_detail(exc, "huggingface")
                self.output_detail = f"{self.output_detail}; {self._last_error}"
                logger.error("Hugging Face speech unavailable: %s", self._last_error)
                self._huggingface = None
                if not self._edge_fallback_ready():
                    return
                logger.warning("Speaking with the Edge fallback voice instead")
                self.output_detail = f"{self.output_detail}; Edge fallback active"
                self._provider = "edge"
                self._last_error = None
            self.available = True
        else:
            self.available = EDGE_TTS_AVAILABLE
        if self.available:
            self._task = asyncio.create_task(self._playback_worker())
            voice = (
                self._config.huggingface_voice
                if self._provider == "huggingface"
                else self._config.voice
            )
            logger.info(
                "TTS ready (provider=%s, voice=%s, player=%s)",
                self._provider,
                voice,
                player,
            )
        else:
            logger.warning("Audio playback is ready, but speech synthesis is unavailable")

    async def speak(self, text: str) -> None:
        if self.available and text.strip():
            await self._queue.put(_SpeechRequest(text))

    async def speak_and_wait(
        self,
        text: str,
        *,
        on_playback_start: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> bool:
        if not self.available or not text.strip():
            return False
        self._last_error = None
        await self._queue.put(_SpeechRequest(text, on_playback_start))
        await self._queue.join()
        return self._last_error is None

    async def _playback_worker(self) -> None:
        while True:
            request = None
            try:
                request = await self._queue.get()
                self._interrupted = False
                self._last_error = None
                await self._synthesize_and_play(
                    request.text,
                    on_playback_start=request.on_playback_start,
                )
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._last_error = _synthesis_error_detail(exc, self._provider)
                if getattr(exc, "status", None) is not None:
                    logger.error("TTS playback error: %s", self._last_error)
                else:
                    logger.exception("TTS playback error")
            finally:
                if request is not None:
                    self._queue.task_done()

    async def _convert_to_wav(self, source: str, destination: str) -> bool:
        if not shutil.which("ffmpeg"):
            return False
        process = await asyncio.create_subprocess_exec(
            "ffmpeg", "-loglevel", "error", "-y", "-i", source, destination,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        return await process.wait() == 0

    def _player_args(self, path: str, is_wav: bool = False) -> list[str]:
        player = self._config.playback_command
        if player == "mpv":
            return ["mpv", "--no-video", "--really-quiet", path]
        if player == "ffplay":
            return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", path]
        if player == "paplay":
            return ["paplay", path]
        if player == "pw-play":
            args = ["pw-play"]
            if self._resolved_target:
                args.extend(["--target", self._resolved_target])
            return [*args, path]
        if player == "aplay":
            return ["aplay", "-q", path]
        raise RuntimeError(f"Unsupported playback command: {player}")

    async def _play_path(self, path: str, is_wav: bool = False) -> None:
        async with self._play_lock:
            self._process = await asyncio.create_subprocess_exec(
                *self._player_args(path, is_wav=is_wav),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            return_code = await self._process.wait()
            self._process = None
            if return_code != 0:
                raise RuntimeError(f"Audio player exited with status {return_code}")

    async def _synthesize_and_play(
        self,
        text: str,
        *,
        on_playback_start: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        self._speaking = True
        paths = []
        try:
            wav_data = None
            if self._provider == "huggingface":
                try:
                    if self._huggingface is None:
                        raise RuntimeError("Hugging Face realtime speech is not connected")
                    wav_data = await self._huggingface.synthesize(text)
                except Exception as exc:
                    if not self._edge_fallback_ready():
                        raise
                    logger.warning(
                        "Hugging Face speech failed (%s); using Edge for this reply",
                        _synthesis_error_detail(exc, "huggingface"),
                    )
            if wav_data is not None:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
                    temp_wav.write(wav_data)
                    wav_path = temp_wav.name
                paths.append(wav_path)
                if self._interrupted:
                    return
                if on_playback_start is not None:
                    await self._announce_envelope(wav_data)
                    try:
                        await on_playback_start()
                    except Exception:
                        logger.exception("Speech playback-start callback failed")
                await self._play_path(wav_path, is_wav=True)
                return

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                mp3_path = temp_file.name
            paths.append(mp3_path)
            communicate = edge_tts.Communicate(
                text,
                voice=self._config.voice,
                rate=self._config.rate,
                pitch=self._config.pitch,
                volume=self._config.volume,
            )
            await communicate.save(mp3_path)
            if self._interrupted:
                return

            playback_path = mp3_path
            is_wav = False
            if self._config.playback_command in {"pw-play", "aplay"}:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
                    wav_path = temp_wav.name
                paths.append(wav_path)
                if not await self._convert_to_wav(mp3_path, wav_path):
                    raise RuntimeError("ffmpeg is required for WAV-only playback commands")
                playback_path = wav_path
                is_wav = True
            if on_playback_start is not None:
                if is_wav:
                    await self._announce_envelope(Path(playback_path).read_bytes())
                try:
                    await on_playback_start()
                except Exception:
                    logger.exception("Speech playback-start callback failed")
            await self._play_path(playback_path, is_wav=is_wav)
        finally:
            self._speaking = False
            self._process = None
            for path in paths:
                try:
                    Path(path).unlink()
                except OSError:
                    pass

    async def _announce_envelope(self, wav_data: bytes) -> None:
        if self._envelope_listener is None:
            return
        try:
            levels, frame_seconds = speech_envelope(wav_data)
            if levels:
                await self._envelope_listener(levels, frame_seconds)
        except Exception:
            logger.exception("Speech envelope listener failed")

    async def play_wav_bytes(self, wav_data: bytes) -> bool:
        """Play a generated cue through the same configured output path."""
        if not self.playback_available:
            return False
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_file:
            temp_file.write(wav_data)
            path = temp_file.name
        try:
            await self._play_path(path, is_wav=True)
            return True
        except (OSError, RuntimeError):
            logger.exception("WAV playback failed")
            return False
        finally:
            try:
                Path(path).unlink()
            except OSError:
                pass

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    @property
    def last_error(self) -> Optional[str]:
        return self._last_error

    def interrupt(self) -> None:
        self._interrupted = True
        if self._process:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

    async def stop(self) -> None:
        self.interrupt()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._huggingface is not None:
            await self._huggingface.stop()
            self._huggingface = None
        self.available = False
        self.playback_available = False
