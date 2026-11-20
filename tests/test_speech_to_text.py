import asyncio

from r08y.config import STTConfig
from r08y.modules import speech_to_text
from r08y.modules.speech_to_text import (
    HuggingFaceRealtimeSTT,
    create_stt_provider,
)


def test_huggingface_provider_requires_token():
    provider = create_stt_provider(
        STTConfig(provider="huggingface", huggingface_token="")
    )

    assert provider is None


def test_huggingface_provider_is_selected_with_token():
    provider = create_stt_provider(
        STTConfig(provider="huggingface", huggingface_token="hf-test")
    )

    assert isinstance(provider, HuggingFaceRealtimeSTT)


def test_huggingface_provider_returns_transcript(monkeypatch):
    class FakeTranscriber:
        def __init__(self, **_kwargs):
            return None

        async def transcribe(self, audio_bytes):
            assert audio_bytes == b"wav"
            return "  Hello, Rob.  "

    async def run():
        monkeypatch.setattr(
            speech_to_text,
            "HuggingFaceTranscriber",
            FakeTranscriber,
        )
        provider = HuggingFaceRealtimeSTT(
            STTConfig(provider="huggingface", huggingface_token="hf-test")
        )
        return await provider.transcribe(b"wav")

    assert asyncio.run(run()) == "Hello, Rob."


def test_fallback_runs_only_when_the_primary_provider_fails():
    from r08y.modules.speech_to_text import FallbackSTT, STTProvider

    class Scripted(STTProvider):
        def __init__(self, text, failed):
            self.text, self.failed, self.calls = text, failed, 0

        async def transcribe(self, audio_bytes):
            self.calls += 1
            self.last_failed = self.failed
            return self.text

    async def run():
        silent = FallbackSTT(Scripted("", False), Scripted("ghost", False), "groq")
        assert await silent.transcribe(b"wav") == ""
        assert silent._fallback.calls == 0

        broken = FallbackSTT(Scripted("", True), Scripted("hello", False), "groq")
        assert await broken.transcribe(b"wav") == "hello"
        assert not broken.last_failed

    asyncio.run(run())


def test_factory_wraps_primary_with_configured_fallback():
    from r08y.modules.speech_to_text import FallbackSTT, GroqWhisperSTT

    with_both = create_stt_provider(
        STTConfig(provider="huggingface", huggingface_token="hf_x", groq_api_key="gsk", fallback_provider="groq")
    )
    assert isinstance(with_both, FallbackSTT)

    without_token = create_stt_provider(
        STTConfig(provider="huggingface", groq_api_key="gsk", fallback_provider="groq")
    )
    assert isinstance(without_token, GroqWhisperSTT)
