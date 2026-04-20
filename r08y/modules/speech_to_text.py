"""Speech-to-text module for R08Y.

Supports OpenAI Whisper and Groq Whisper APIs behind a common interface.
Sends WAV audio bytes and returns transcribed text.
"""

import asyncio
import io
import logging
from abc import ABC, abstractmethod
from typing import Optional

from r08y.config import STTConfig

logger = logging.getLogger(__name__)


class STTProvider(ABC):
    """Abstract speech-to-text provider."""

    @abstractmethod
    async def transcribe(self, audio_bytes: bytes) -> str:
        """Transcribe WAV audio bytes to text. Returns empty string on failure."""
        ...


class OpenAIWhisperSTT(STTProvider):
    """Speech-to-text via OpenAI Whisper API."""

    def __init__(self, config: STTConfig):
        self._config = config
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(api_key=self._config.openai_api_key)
        return self._client

    async def transcribe(self, audio_bytes: bytes) -> str:
        try:
            client = await self._get_client()
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "speech.wav"

            response = await client.audio.transcriptions.create(
                model=self._config.model if "whisper" in self._config.model else "whisper-1",
                file=audio_file,
                language=self._config.language,
            )
            text = response.text.strip()
            logger.info("OpenAI STT: '%s'", text[:100])
            return text
        except Exception:
            logger.exception("OpenAI Whisper transcription failed")
            return ""


class GroqWhisperSTT(STTProvider):
    """Speech-to-text via Groq Whisper API (faster, free tier available)."""

    def __init__(self, config: STTConfig):
        self._config = config
        self._client = None

    async def _get_client(self):
        if self._client is None:
            from groq import AsyncGroq
            self._client = AsyncGroq(api_key=self._config.groq_api_key)
        return self._client

    async def transcribe(self, audio_bytes: bytes) -> str:
        try:
            client = await self._get_client()
            audio_file = io.BytesIO(audio_bytes)
            audio_file.name = "speech.wav"

            response = await client.audio.transcriptions.create(
                model=self._config.model,
                file=audio_file,
                language=self._config.language,
            )
            text = response.text.strip()
            logger.info("Groq STT: '%s'", text[:100])
            return text
        except Exception:
            logger.exception("Groq Whisper transcription failed")
            return ""


def create_stt_provider(config: STTConfig) -> Optional[STTProvider]:
    """Factory: create the configured STT provider."""
    if config.provider == "openai":
        if not config.openai_api_key or config.openai_api_key.startswith("YOUR_"):
            logger.warning("OpenAI API key not configured — STT disabled")
            return None
        return OpenAIWhisperSTT(config)
    elif config.provider == "groq":
        if not config.groq_api_key or config.groq_api_key.startswith("YOUR_"):
            logger.warning("Groq API key not configured — STT disabled")
            return None
        return GroqWhisperSTT(config)
    else:
        logger.error("Unknown STT provider: %s", config.provider)
        return None
