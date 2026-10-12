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
