"""Text-to-speech and named-device playback for R08Y."""

from __future__ import annotations

import asyncio
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from r08y.config import TTSConfig
from r08y.modules.audio_devices import PipeWireResolver

logger = logging.getLogger(__name__)

try:
    import edge_tts

    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts not installed; speech synthesis disabled")


ALLOWED_PLAYERS = {"mpv", "ffplay", "paplay", "pw-play", "aplay"}


class TextToSpeech:
    """Queue Edge TTS speech and route playback through the configured sink."""

    def __init__(self, config: TTSConfig):
        self._config = config
        self._speaking = False
        self._process: Optional[asyncio.subprocess.Process] = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._interrupted = False
        self._play_lock = asyncio.Lock()
        self._resolved_target: Optional[str] = config.output_target
        self.playback_available = False
        self.available = False

    def _select_player(self) -> Optional[str]:
        requested = self._config.playback_command
        if requested not in ALLOWED_PLAYERS:
            logger.error("Playback command '%s' is not allowed", requested)
            return None
        if shutil.which(requested):
            return requested
        for fallback in ("mpv", "pw-play", "ffplay", "paplay", "aplay"):
            if shutil.which(fallback):
                logger.warning("Playback command '%s' not found; using %s", requested, fallback)
                self._config.playback_command = fallback
                return fallback
        return None

    async def start(self) -> None:
        player = self._select_player()
        if player is None:
            logger.error("No supported audio playback command is installed")
            return

        if self._config.output_device_name:
            resolver = PipeWireResolver()
            if self._config.set_pipewire_default:
                selected = await asyncio.to_thread(
                    resolver.configure_defaults, self._config.output_device_name
                )
                sink = selected.get("sink")
                if sink:
                    self._resolved_target = sink.id
                    logger.info("Audio output selected: '%s' (PipeWire node %s)", sink.name, sink.id)
                elif resolver.available:
                    log = logger.error if self._config.require_output_device_match else logger.warning
                    log(
                        "PipeWire sink matching '%s' was not found; using current default",
                        self._config.output_device_name,
                    )
                    if self._config.require_output_device_match:
                        return
                elif self._config.require_output_device_match and not self._resolved_target:
                    logger.error("wpctl is required for strict named audio-output selection")
                    return
            elif not self._resolved_target:
                sink = await asyncio.to_thread(
                    resolver.resolve, "sink", self._config.output_device_name
                )
                if sink:
                    self._resolved_target = sink.id
                elif self._config.require_output_device_match:
                    logger.error(
                        "Audio output matching '%s' was not found",
                        self._config.output_device_name,
                    )
                    return

        self.playback_available = True
        self.available = EDGE_TTS_AVAILABLE
        if self.available:
            self._task = asyncio.create_task(self._playback_worker())
            logger.info("TTS ready (voice=%s, player=%s)", self._config.voice, player)
        else:
            logger.warning("Audio playback is ready, but speech synthesis is unavailable")

    async def speak(self, text: str) -> None:
        if self.available and text.strip():
            await self._queue.put(text)

    async def speak_and_wait(self, text: str) -> None:
        if not self.available or not text.strip():
            return
        await self._queue.put(text)
        await self._queue.join()

    async def _playback_worker(self) -> None:
        while True:
            text = None
            try:
                text = await self._queue.get()
                self._interrupted = False
                await self._synthesize_and_play(text)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("TTS playback error")
            finally:
                if text is not None:
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

    async def _synthesize_and_play(self, text: str) -> None:
        self._speaking = True
        paths = []
        try:
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as temp_file:
                mp3_path = temp_file.name
            paths.append(mp3_path)
            communicate = edge_tts.Communicate(
                text,
                voice=self._config.voice,
                rate=self._config.rate,
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
            await self._play_path(playback_path, is_wav=is_wav)
        finally:
            self._speaking = False
            self._process = None
            for path in paths:
                try:
                    Path(path).unlink()
                except OSError:
                    pass

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
        self.available = False
        self.playback_available = False
