"""Speech-to-text providers behind R08Y's normalized WAV interface."""

import asyncio
import io
import logging
from abc import ABC, abstractmethod
from typing import Optional

from r08y.config import STTConfig
from r08y.modules.huggingface_speech import HuggingFaceTranscriber, safe_exception_detail

logger = logging.getLogger(__name__)

OPENAI_STT_MODELS = {"whisper-1", "gpt-4o-transcribe", "gpt-4o-mini-transcribe"}


class STTProvider(ABC):
    """Abstract speech-to-text provider."""

    # Set by providers when the last transcription raised instead of hearing
    # silence, so a fallback runs only for real failures.
    last_failed: bool = False

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

            # The shared model setting usually names Groq's whisper-large-v3,
            # which OpenAI does not serve.
            response = await client.audio.transcriptions.create(
                model=self._config.model if self._config.model in OPENAI_STT_MODELS else "whisper-1",
                file=audio_file,
                language=self._config.language,
            )
            text = response.text.strip()
            logger.info("OpenAI STT: '%s'", text[:100])
            self.last_failed = False
            return text
        except Exception:
            logger.exception("OpenAI Whisper transcription failed")
            self.last_failed = True
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
            self.last_failed = False
            return text
        except Exception:
            logger.exception("Groq Whisper transcription failed")
            self.last_failed = True
            return ""


class HuggingFaceRealtimeSTT(STTProvider):
    """Speech-to-text through the authenticated Reachy Mini realtime service."""

    def __init__(self, config: STTConfig):
        self._transcriber = HuggingFaceTranscriber(
            token=config.huggingface_token,
            session_url=config.huggingface_session_url,
            language=config.language,
            timeout_seconds=config.huggingface_timeout_seconds,
        )

    async def transcribe(self, audio_bytes: bytes) -> str:
        try:
            text = (await self._transcriber.transcribe(audio_bytes)).strip()
            logger.info("Hugging Face STT: '%s'", text[:100])
            self.last_failed = False
            return text
        except Exception as exc:
            logger.error("Hugging Face transcription failed: %s", safe_exception_detail(exc))
            self.last_failed = True
            return ""


class FallbackSTT(STTProvider):
    """Use a second provider when the first one fails, not when it hears silence."""

    def __init__(self, primary: STTProvider, fallback: STTProvider, fallback_name: str):
        self._primary = primary
        self._fallback = fallback
        self._fallback_name = fallback_name

    async def transcribe(self, audio_bytes: bytes) -> str:
        text = await self._primary.transcribe(audio_bytes)
        if text or not self._primary.last_failed:
            self.last_failed = False
            return text
        logger.warning("Primary STT failed; retrying with %s", self._fallback_name)
        text = await self._fallback.transcribe(audio_bytes)
        self.last_failed = self._fallback.last_failed
        return text


def _create_single_provider(provider: str, config: STTConfig) -> Optional[STTProvider]:
    if provider == "huggingface":
        if not config.huggingface_token or config.huggingface_token.startswith("YOUR_"):
            logger.warning("Hugging Face token not configured; STT disabled")
            return None
        return HuggingFaceRealtimeSTT(config)
    if provider == "openai":
        if not config.openai_api_key or config.openai_api_key.startswith("YOUR_"):
            logger.warning("OpenAI API key not configured — STT disabled")
            return None
        return OpenAIWhisperSTT(config)
    if provider == "groq":
        if not config.groq_api_key or config.groq_api_key.startswith("YOUR_"):
            logger.warning("Groq API key not configured — STT disabled")
            return None
        return GroqWhisperSTT(config)
    logger.error("Unknown STT provider: %s", provider)
    return None


def create_stt_provider(config: STTConfig) -> Optional[STTProvider]:
    """Factory: create the configured STT provider and optional fallback."""
    primary = _create_single_provider(config.provider, config)
    if not config.fallback_provider:
        return primary
    fallback = _create_single_provider(config.fallback_provider, config)
    if fallback is None:
        return primary
    if primary is None:
        logger.warning("Primary STT unavailable; using %s", config.fallback_provider)
        return fallback
    return FallbackSTT(primary, fallback, config.fallback_provider)
