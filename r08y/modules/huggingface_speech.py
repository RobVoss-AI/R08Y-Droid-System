"""Hugging Face realtime speech synthesis for R08Y.

The protocol shape follows Pollen Robotics' Apache-2.0 Reachy Mini
conversation app while keeping R08Y's existing brain and audio routing.
"""

from __future__ import annotations

import asyncio
import base64
import io
import logging
import re
import wave
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import httpx

try:
    from openai import AsyncOpenAI

    OPENAI_REALTIME_AVAILABLE = True
except ImportError:
    AsyncOpenAI = None  # type: ignore[assignment,misc]
    OPENAI_REALTIME_AVAILABLE = False

logger = logging.getLogger(__name__)

DEFAULT_SESSION_URL = (
    "https://pollen-robotics-reachy-mini-realtime-url.hf.space/session"
)
HUGGINGFACE_VOICES = {
    "Aiden",
    "Ryan",
    "Dylan",
    "Eric",
    "Ono_Anna",
    "Serena",
    "Sohee",
    "Uncle_Fu",
    "Vivian",
}
PCM_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class RealtimeURL:
    base_url: str
    websocket_base_url: str
    connect_query: dict[str, str]


def parse_realtime_url(url: str) -> RealtimeURL:
    """Convert an allocated websocket URL into OpenAI client settings."""
    parsed = urlsplit(url)
    scheme = parsed.scheme.casefold()
    if scheme not in {"ws", "wss", "http", "https"}:
        raise ValueError("Hugging Face realtime URL must use HTTP or WebSocket")

    path = parsed.path.rstrip("/")
    if not path.endswith("/realtime"):
        raise ValueError("Hugging Face realtime URL must end with /realtime")
    base_path = path[: -len("/realtime")]
    http_scheme = "https" if scheme in {"https", "wss"} else "http"
    websocket_scheme = "wss" if scheme in {"https", "wss"} else "ws"
    query = {
        key: value
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key != "model"
    }
    return RealtimeURL(
        base_url=urlunsplit((http_scheme, parsed.netloc, base_path, "", "")),
        websocket_base_url=urlunsplit(
            (websocket_scheme, parsed.netloc, base_path, "", "")
        ),
        connect_query=query,
    )


def pcm_to_wav(pcm: bytes, sample_rate: int = PCM_SAMPLE_RATE) -> bytes:
    """Wrap signed 16-bit mono PCM in a WAV container for PipeWire."""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm)
    return output.getvalue()


def _safe_error_detail(error: Any) -> str:
    """Return a bounded provider error without exposing signed URLs or tokens."""
    code = str(getattr(error, "code", "") or getattr(error, "type", "")).strip()
    message = str(getattr(error, "message", "") or "provider error").strip()
    message = re.sub(r"(?:https?|wss?)://\S+", "[provider URL hidden]", message)
    message = re.sub(r"hf_[A-Za-z0-9_-]+", "[Hugging Face token hidden]", message)
    message = message[:180]
    return f"{code}: {message}" if code else message


def safe_exception_detail(exc: Exception) -> str:
    """Sanitize an exception raised by an authenticated provider client."""
    return _safe_error_detail(
        type("ProviderError", (), {"message": str(exc), "code": type(exc).__name__})()
    )


async def _allocate_realtime(
    token: str,
    session_url: str,
) -> tuple[Any, Any, Any]:
    """Allocate and open one authenticated Hugging Face realtime connection."""
    headers = {
        "User-Agent": "r08y-droid-system",
        "X-Reachy-Mini-Authorization": f"Bearer {token}",
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as http_client:
            response = await http_client.post(session_url, headers=headers, json={})
            response.raise_for_status()
            payload = response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 401:
            raise RuntimeError(
                "Hugging Face rejected the token. Use a verified personal read token."
            ) from None
        raise RuntimeError(
            f"Hugging Face session allocation returned HTTP {exc.response.status_code}"
        ) from None
    except (httpx.HTTPError, ValueError) as exc:
        raise RuntimeError(
            f"Hugging Face session allocation failed: {type(exc).__name__}"
        ) from None

    connect_url = payload.get("connect_url") if isinstance(payload, dict) else None
    if not isinstance(connect_url, str) or not connect_url:
        raise RuntimeError("Hugging Face session allocation returned no connection")
    if urlsplit(connect_url).scheme.casefold() not in {"https", "wss"}:
        raise RuntimeError("Hugging Face returned an insecure realtime connection")
    realtime_url = parse_realtime_url(connect_url)

    client = AsyncOpenAI(
        api_key=token,
        base_url=realtime_url.base_url,
        websocket_base_url=realtime_url.websocket_base_url,
    )
    manager = client.realtime.connect(extra_query=realtime_url.connect_query)
    try:
        connection = await manager.__aenter__()
    except Exception:
        await client.close()
        raise
    return client, manager, connection


class HuggingFaceSpeech:
    """Maintain one authenticated realtime session and return spoken WAV audio."""

    def __init__(
        self,
        *,
        token: str,
        session_url: str = DEFAULT_SESSION_URL,
        voice: str = "Aiden",
        instructions: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._token = token.strip()
        self._session_url = session_url.strip()
        self._voice = voice
        self._instructions = instructions.strip()
        self._timeout_seconds = timeout_seconds
        self._client: Any = None
        self._connection_manager: Any = None
        self._connection: Any = None
        self._receiver_task: Optional[asyncio.Task] = None
        self._request_lock = asyncio.Lock()
        self._audio_chunks: list[bytes] = []
        self._result: Optional[asyncio.Future[bytes]] = None
        self._expected_text = ""
        self._spoken_transcript = ""

    @property
    def voice(self) -> str:
        return self._voice

    async def start(self) -> None:
        if not OPENAI_REALTIME_AVAILABLE:
            raise RuntimeError("openai>=2.28 is required for Hugging Face realtime speech")
        if not self._token or self._token.startswith("YOUR_"):
            raise RuntimeError(
                "Hugging Face realtime requires R08Y_HF_TOKEN in /etc/r08y/r08y.env"
            )
        if self._voice not in HUGGINGFACE_VOICES:
            raise RuntimeError(
                "Unsupported Hugging Face voice; choose "
                + ", ".join(sorted(HUGGINGFACE_VOICES))
            )

        (
            self._client,
            self._connection_manager,
            self._connection,
        ) = await _allocate_realtime(
            self._token,
            self._session_url,
        )
        try:
            await self._connection.session.update(
                session={
                    "type": "realtime",
                    "instructions": self._instructions,
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": None},
                            "turn_detection": None,
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": None},
                            "voice": self._voice,
                        },
                    },
                }
            )
        except Exception:
            await self.stop()
            raise
        self._receiver_task = asyncio.create_task(
            self._receive_events(),
            name="huggingface-speech-events",
        )

    async def _receive_events(self) -> None:
        try:
            async for event in self._connection:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_audio.delta":
                    delta = getattr(event, "delta", "")
                    if delta:
                        self._audio_chunks.append(base64.b64decode(delta))
                elif event_type == "response.output_audio_transcript.done":
                    self._spoken_transcript = str(
                        getattr(event, "transcript", "") or ""
                    ).strip()
                elif event_type == "response.done":
                    self._finish_request()
                elif event_type == "error":
                    detail = _safe_error_detail(getattr(event, "error", None))
                    self._fail_request(RuntimeError(f"Hugging Face realtime error: {detail}"))
            self._fail_request(RuntimeError("Hugging Face realtime connection closed"))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._fail_request(
                RuntimeError(
                    f"Hugging Face realtime connection closed: {type(exc).__name__}"
                )
            )

    def _finish_request(self) -> None:
        if self._result is None or self._result.done():
            return
        if not self._audio_chunks:
            self._result.set_exception(RuntimeError("Hugging Face returned no speech audio"))
            return
        if (
            self._spoken_transcript
            and self._expected_text
            and self._spoken_transcript.casefold() != self._expected_text.casefold()
        ):
            logger.warning("Hugging Face adjusted the requested spoken wording")
        self._result.set_result(pcm_to_wav(b"".join(self._audio_chunks)))

    def _fail_request(self, exc: Exception) -> None:
        if self._result is not None and not self._result.done():
            self._result.set_exception(exc)

    async def synthesize(self, text: str) -> bytes:
        if self._connection is None:
            raise RuntimeError("Hugging Face realtime session is not connected")
        text = text.strip()
        if not text:
            raise ValueError("Speech text must not be empty")

        async with self._request_lock:
            loop = asyncio.get_running_loop()
            self._audio_chunks = []
            self._expected_text = text
            self._spoken_transcript = ""
            self._result = loop.create_future()
            try:
                await self._connection.conversation.item.create(
                    item={
                        "type": "message",
                        "role": "user",
                        "content": [
                            {
                                "type": "input_text",
                                "text": (
                                    "Read the following line aloud exactly. Do not add, remove, "
                                    f"or rephrase any words:\n{text}"
                                ),
                            }
                        ],
                    }
                )
                await self._connection.response.create()
                return await asyncio.wait_for(
                    self._result,
                    timeout=self._timeout_seconds,
                )
            except asyncio.TimeoutError:
                raise RuntimeError("Hugging Face speech timed out") from None
            finally:
                self._result = None

    async def stop(self) -> None:
        if self._receiver_task is not None:
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None
        self._fail_request(RuntimeError("Hugging Face speech stopped"))
        if self._connection_manager is not None:
            try:
                await self._connection_manager.__aexit__(None, None, None)
            except Exception:
                logger.debug("Hugging Face realtime close failed", exc_info=True)
        if self._client is not None:
            try:
                await self._client.close()
            except Exception:
                logger.debug("Hugging Face client close failed", exc_info=True)
        self._connection = None
        self._connection_manager = None
        self._client = None


class HuggingFaceTranscriber:
    """Transcribe one normalized R08Y WAV recording through realtime input."""

    def __init__(
        self,
        *,
        token: str,
        session_url: str = DEFAULT_SESSION_URL,
        language: str = "en",
        timeout_seconds: float = 30.0,
    ) -> None:
        self._token = token.strip()
        self._session_url = session_url.strip()
        self._language = language
        self._timeout_seconds = timeout_seconds

    @staticmethod
    def _read_pcm(wav_data: bytes) -> bytes:
        try:
            with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
                if (
                    wav_file.getframerate() != PCM_SAMPLE_RATE
                    or wav_file.getnchannels() != 1
                    or wav_file.getsampwidth() != 2
                ):
                    raise ValueError(
                        "Hugging Face transcription requires 16 kHz mono S16_LE WAV"
                    )
                return wav_file.readframes(wav_file.getnframes())
        except wave.Error:
            raise ValueError("Hugging Face transcription received an invalid WAV") from None

    async def _wait_for_transcript(self, connection: Any) -> str:
        async for event in connection:
            event_type = getattr(event, "type", "")
            if event_type == "conversation.item.input_audio_transcription.completed":
                return str(getattr(event, "transcript", "") or "").strip()
            if event_type == "error":
                detail = _safe_error_detail(getattr(event, "error", None))
                raise RuntimeError(f"Hugging Face realtime error: {detail}")
        raise RuntimeError("Hugging Face realtime closed before transcription")

    async def transcribe(self, wav_data: bytes) -> str:
        if not OPENAI_REALTIME_AVAILABLE:
            raise RuntimeError("openai>=2.28 is required for Hugging Face realtime")
        if not self._token or self._token.startswith("YOUR_"):
            raise RuntimeError(
                "Hugging Face realtime requires R08Y_HF_TOKEN in /etc/r08y/r08y.env"
            )
        pcm = self._read_pcm(wav_data)
        if not pcm:
            raise ValueError("Hugging Face transcription received empty audio")
        client = manager = connection = None
        transcript_task: Optional[asyncio.Task[str]] = None
        try:
            client, manager, connection = await _allocate_realtime(
                self._token,
                self._session_url,
            )
            await connection.session.update(
                session={
                    "type": "realtime",
                    "audio": {
                        "input": {
                            "format": {"type": "audio/pcm", "rate": None},
                            "transcription": {
                                "model": "gpt-4o-transcribe",
                                "language": self._language,
                            },
                            "turn_detection": None,
                        },
                        "output": {
                            "format": {"type": "audio/pcm", "rate": None},
                            "voice": "Aiden",
                        },
                    },
                }
            )
            transcript_task = asyncio.create_task(self._wait_for_transcript(connection))
            for offset in range(0, len(pcm), PCM_SAMPLE_RATE * 2):
                chunk = pcm[offset : offset + PCM_SAMPLE_RATE * 2]
                await connection.input_audio_buffer.append(
                    audio=base64.b64encode(chunk).decode("ascii")
                )
            await connection.input_audio_buffer.commit()
            return await asyncio.wait_for(
                transcript_task,
                timeout=self._timeout_seconds,
            )
        except asyncio.TimeoutError:
            raise RuntimeError("Hugging Face transcription timed out") from None
        finally:
            if transcript_task is not None and not transcript_task.done():
                transcript_task.cancel()
                try:
                    await transcript_task
                except asyncio.CancelledError:
                    pass
            if manager is not None:
                try:
                    await manager.__aexit__(None, None, None)
                except Exception:
                    logger.debug("Hugging Face transcription close failed", exc_info=True)
            if client is not None:
                try:
                    await client.close()
                except Exception:
                    logger.debug("Hugging Face transcription client close failed", exc_info=True)
            del connection, client
