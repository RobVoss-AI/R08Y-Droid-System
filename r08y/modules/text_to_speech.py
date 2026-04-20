"""Text-to-speech module for R08Y.

Uses Microsoft Edge TTS (free, natural voices) to convert AI responses to
speech audio, then plays via system audio. Supports a speech queue and
interruption.
"""

import asyncio
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from r08y.config import TTSConfig

logger = logging.getLogger(__name__)

try:
    import edge_tts
    EDGE_TTS_AVAILABLE = True
except ImportError:
    EDGE_TTS_AVAILABLE = False
    logger.warning("edge-tts not installed — text-to-speech disabled")


# Allowed playback commands (whitelist for safety)
ALLOWED_PLAYERS = {"aplay", "mpv", "ffplay", "paplay"}


class TextToSpeech:
    """Async text-to-speech using Microsoft Edge TTS."""

    def __init__(self, config: TTSConfig):
        self._config = config
        self._speaking = False
        self._process: Optional[asyncio.subprocess.Process] = None
        self._queue: asyncio.Queue[str] = asyncio.Queue()
        self._task: Optional[asyncio.Task] = None
        self._interrupted = False
        self.available = False

    async def start(self) -> None:
        """Start the TTS playback worker."""
        if not EDGE_TTS_AVAILABLE:
            return

        # Validate playback command against whitelist
        if self._config.playback_command not in ALLOWED_PLAYERS:
            logger.warning(
                "Playback command '%s' not in allowed list %s — defaulting to mpv",
                self._config.playback_command, ALLOWED_PLAYERS,
            )
            self._config.playback_command = "mpv"

        self.available = True
        self._task = asyncio.create_task(self._playback_worker())
        logger.info("TTS ready (voice: %s, player: %s)", self._config.voice, self._config.playback_command)

    async def speak(self, text: str) -> None:
        """Queue text for speech synthesis and playback."""
        if not self.available or not text.strip():
            return
        await self._queue.put(text)

    async def speak_and_wait(self, text: str) -> None:
        """Speak text and wait for playback to complete."""
        if not self.available or not text.strip():
            return
        await self._queue.put(text)
        # Wait for queue to drain and speaking to finish
        while not self._queue.empty() or self._speaking:
            await asyncio.sleep(0.1)

    async def _playback_worker(self) -> None:
        """Process the speech queue sequentially."""
        while True:
            try:
                text = await self._queue.get()
                self._interrupted = False
                await self._synthesize_and_play(text)
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("TTS playback error")

    async def _synthesize_and_play(self, text: str) -> None:
        """Convert text to speech audio and play it."""
        self._speaking = True
        tmp_path = None
        try:
            # Synthesize to temporary file
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_path = f.name

            communicate = edge_tts.Communicate(
                text,
                voice=self._config.voice,
                rate=self._config.rate,
                volume=self._config.volume,
            )
            await communicate.save(tmp_path)

            if self._interrupted:
                return

            # Build playback command args (no shell — direct exec for safety)
            player = self._config.playback_command
            if player == "mpv":
                args = ["mpv", "--no-video", "--really-quiet", tmp_path]
            elif player == "aplay":
                # aplay only handles WAV; for mp3, use mpv as fallback
                args = ["mpv", "--no-video", "--really-quiet", tmp_path]
            elif player == "ffplay":
                args = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", tmp_path]
            elif player == "paplay":
                args = ["paplay", tmp_path]
            else:
                args = ["mpv", "--no-video", "--really-quiet", tmp_path]

            logger.debug("Playing speech: '%s'", text[:50])
            self._process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await self._process.wait()

        finally:
            self._speaking = False
            self._process = None
            # Clean up temp file
            if tmp_path:
                try:
                    Path(tmp_path).unlink()
                except OSError:
                    pass

    @property
    def is_speaking(self) -> bool:
        return self._speaking

    def interrupt(self) -> None:
        """Interrupt current speech playback (e.g., when wake word detected)."""
        self._interrupted = True
        if self._process:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
        # Clear the queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break
        logger.debug("Speech interrupted")

    async def stop(self) -> None:
        """Stop the TTS worker and clean up."""
        self.interrupt()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.available = False
