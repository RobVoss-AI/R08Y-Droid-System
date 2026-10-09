import asyncio

from r08y.config import TTSConfig
from r08y.modules.audio_devices import AudioDevice
from r08y.modules import text_to_speech
from r08y.modules.text_to_speech import TextToSpeech


class MissingSinkResolver:
    available = True

    def resolve(self, _kind, _name):
        return None

    def set_default(self, _device):
        raise AssertionError("missing sink cannot be made default")


class MatchingSinkResolver:
    available = True
    default_device = None

    def resolve(self, kind, _name):
        assert kind == "sink"
        return AudioDevice("89", "reSpeaker XVF3800", "sink")

    def set_default(self, device):
        type(self).default_device = device


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
        assert "XVF3800" in tts.output_detail
        assert MatchingSinkResolver.default_device.id == "89"
        await tts.stop()

    asyncio.run(run())


def test_pw_play_targets_discovered_sink():
    tts = TextToSpeech(TTSConfig(playback_command="pw-play"))
    tts._resolved_target = "89"
    assert tts._player_args("speech.wav", is_wav=True) == [
        "pw-play",
        "--target",
        "89",
        "speech.wav",
    ]
