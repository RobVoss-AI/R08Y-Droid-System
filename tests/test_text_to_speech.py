import asyncio

from r08y.config import TTSConfig
from r08y.modules.audio_devices import AudioDevice
from r08y.modules import text_to_speech
from r08y.modules.text_to_speech import TextToSpeech


class MissingSinkResolver:
    available = True

    def configure_defaults(self, _name):
        return {"sink": None, "source": None}


class MatchingSinkResolver:
    available = True

    def configure_defaults(self, _name):
        return {
            "sink": AudioDevice("89", "reSpeaker XVF3800", "sink"),
            "source": None,
        }


def test_strict_named_output_does_not_fall_back(monkeypatch):
    async def run():
        monkeypatch.setattr(text_to_speech, "PipeWireResolver", MissingSinkResolver)
        tts = TextToSpeech(TTSConfig(require_output_device_match=True))
        monkeypatch.setattr(tts, "_select_player", lambda: "mpv")
        await tts.start()
        assert not tts.playback_available
        assert not tts.available
        await tts.stop()

    asyncio.run(run())


def test_matching_named_output_enables_playback(monkeypatch):
    async def run():
        monkeypatch.setattr(text_to_speech, "PipeWireResolver", MatchingSinkResolver)
        tts = TextToSpeech(TTSConfig(require_output_device_match=True))
        monkeypatch.setattr(tts, "_select_player", lambda: "mpv")
        await tts.start()
        assert tts.playback_available
        assert tts._resolved_target == "89"
        await tts.stop()

    asyncio.run(run())
