import asyncio
import base64
import io
import wave
from types import SimpleNamespace

import pytest

from r08y.modules import huggingface_speech
from r08y.modules.huggingface_speech import (
    HuggingFaceSpeech,
    HuggingFaceTranscriber,
    _safe_error_detail,
    parse_realtime_url,
    pcm_to_wav,
    safe_exception_detail,
)


def _speech(**overrides):
    values = {
        "token": "hf-test",
        "voice": "Aiden",
        "instructions": "Speak naturally.",
    }
    values.update(overrides)
    return HuggingFaceSpeech(**values)


def test_parse_realtime_url_preserves_session_token_not_model():
    parsed = parse_realtime_url(
        "wss://voice.example/v1/realtime?session_token=abc&model=server-owned"
    )

    assert parsed.base_url == "https://voice.example/v1"
    assert parsed.websocket_base_url == "wss://voice.example/v1"
    assert parsed.connect_query == {"session_token": "abc"}


def test_pcm_is_wrapped_as_16khz_mono_wav():
    wav_data = pcm_to_wav(b"\x01\x00\x02\x00")

    with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
        assert wav_file.getframerate() == 16000
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.readframes(2) == b"\x01\x00\x02\x00"


def test_missing_token_fails_before_network():
    async def run():
        speech = _speech(token="")
        with pytest.raises(RuntimeError, match="R08Y_HF_TOKEN"):
            await speech.start()

    asyncio.run(run())


def test_allocator_and_session_connection_use_token_without_logging_it(monkeypatch):
    requests = []
    client_args = {}
    connect_args = {}
    session_updates = []

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "connect_url": (
                    "wss://voice.example/v1/realtime?session_token=allocated&model=ignored"
                )
            }

    class FakeHTTPClient:
        def __init__(self, **_kwargs):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def post(self, url, *, headers, json):
            requests.append((url, headers, json))
            return FakeResponse()

    class FakeSession:
        async def update(self, **kwargs):
            session_updates.append(kwargs)

    class FakeConnection:
        session = FakeSession()

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    class FakeManager:
        async def __aenter__(self):
            return FakeConnection()

        async def __aexit__(self, *_args):
            return False

    class FakeRealtime:
        def connect(self, **kwargs):
            connect_args.update(kwargs)
            return FakeManager()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            client_args.update(kwargs)
            self.realtime = FakeRealtime()

    async def run():
        monkeypatch.setattr(huggingface_speech.httpx, "AsyncClient", FakeHTTPClient)
        monkeypatch.setattr(huggingface_speech, "AsyncOpenAI", FakeOpenAI)
        speech = _speech(token="hf-secret")
        await speech.start()
        await asyncio.sleep(0)
        await speech.stop()

    asyncio.run(run())

    assert requests[0][1]["X-Reachy-Mini-Authorization"] == "Bearer hf-secret"
    assert client_args["api_key"] == "hf-secret"
    assert client_args["base_url"] == "https://voice.example/v1"
    assert connect_args == {"extra_query": {"session_token": "allocated"}}
    assert session_updates[0]["session"]["audio"]["output"]["voice"] == "Aiden"


def test_synthesize_collects_pcm_and_requests_exact_wording():
    captured_items = []
    response_calls = []

    class FakeItem:
        async def create(self, **kwargs):
            captured_items.append(kwargs)

    class FakeResponse:
        async def create(self):
            response_calls.append(True)

    async def run():
        speech = _speech()
        speech._connection = SimpleNamespace(
            conversation=SimpleNamespace(item=FakeItem()),
            response=FakeResponse(),
        )
        task = asyncio.create_task(speech.synthesize("Hello, Rob."))
        await asyncio.sleep(0)
        speech._audio_chunks.append(base64.b64decode(base64.b64encode(b"\x01\x00")))
        speech._spoken_transcript = "Hello, Rob."
        speech._finish_request()
        wav_data = await task
        return wav_data

    wav_data = asyncio.run(run())

    assert wav_data.startswith(b"RIFF")
    assert "Hello, Rob." in captured_items[0]["item"]["content"][0]["text"]
    assert response_calls == [True]


def test_clean_connection_close_fails_pending_speech_immediately():
    class ClosedConnection:
        def __aiter__(self):
            return self

        async def __anext__(self):
            raise StopAsyncIteration

    async def run():
        speech = _speech()
        speech._connection = ClosedConnection()
        speech._result = asyncio.get_running_loop().create_future()

        await speech._receive_events()

        with pytest.raises(RuntimeError, match="connection closed"):
            await speech._result

    asyncio.run(run())


def test_provider_error_hides_signed_urls():
    error = SimpleNamespace(
        code="provider_failure",
        message="failed at wss://voice.example/realtime?session_token=secret",
    )

    detail = _safe_error_detail(error)

    assert "provider_failure" in detail
    assert "secret" not in detail
    assert "provider URL hidden" in detail


def test_exception_detail_hides_huggingface_token():
    detail = safe_exception_detail(
        RuntimeError("request failed for hf_secret123 at wss://voice.example/token")
    )

    assert "hf_secret123" not in detail
    assert "voice.example" not in detail


def test_transcriber_sends_wav_pcm_and_returns_completed_text(monkeypatch):
    appended = []
    committed = []
    session_updates = []
    closed = []

    class FakeSession:
        async def update(self, **kwargs):
            session_updates.append(kwargs)

    class FakeInputBuffer:
        async def append(self, **kwargs):
            appended.append(kwargs["audio"])

        async def commit(self):
            committed.append(True)

    class FakeConnection:
        session = FakeSession()
        input_audio_buffer = FakeInputBuffer()

        def __init__(self):
            self.events = iter(
                [
                    SimpleNamespace(
                        type="conversation.item.input_audio_transcription.completed",
                        transcript="Hello, R08Y.",
                    )
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.events)
            except StopIteration:
                raise StopAsyncIteration

    class FakeManager:
        async def __aexit__(self, *_args):
            closed.append(True)

    async def fake_allocate(_token, _session_url):
        return object(), FakeManager(), FakeConnection()

    async def run():
        monkeypatch.setattr(
            huggingface_speech,
            "_allocate_realtime",
            fake_allocate,
        )
        transcriber = HuggingFaceTranscriber(
            token="hf-test",
            language="en",
        )
        return await transcriber.transcribe(pcm_to_wav(b"\x01\x00\x02\x00"))

    transcript = asyncio.run(run())

    assert transcript == "Hello, R08Y."
    assert base64.b64decode(appended[0]) == b"\x01\x00\x02\x00"
    assert committed == [True]
    assert session_updates[0]["session"]["audio"]["input"]["transcription"]["language"] == "en"
    assert closed == [True]


def test_transcriber_rejects_non_native_wav():
    wav_data = pcm_to_wav(b"\x00\x00", sample_rate=8000)

    with pytest.raises(ValueError, match="16 kHz mono"):
        HuggingFaceTranscriber._read_pcm(wav_data)
