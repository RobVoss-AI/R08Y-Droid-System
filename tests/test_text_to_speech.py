import asyncio
from pathlib import Path
from types import SimpleNamespace

from r08y.config import TTSConfig
from r08y.modules.audio_devices import AudioDevice
from r08y.modules import text_to_speech
from r08y.modules.text_to_speech import TextToSpeech, _synthesis_error_detail


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


def test_synthesis_passes_configured_voice_tone(monkeypatch):
    captured = {}

    class FakeCommunicate:
        def __init__(self, text, **kwargs):
            captured["text"] = text
            captured.update(kwargs)

        async def save(self, path):
            Path(path).write_bytes(b"mp3")

    async def run():
        monkeypatch.setattr(
            text_to_speech,
            "edge_tts",
            SimpleNamespace(Communicate=FakeCommunicate),
        )
        tts = TextToSpeech(
            TTSConfig(
                voice="en-US-GuyNeural",
                rate="-5%",
                pitch="-8Hz",
                volume="+0%",
                playback_command="mpv",
            )
        )

        async def fake_play(_path, is_wav=False):
            assert not is_wav

        monkeypatch.setattr(tts, "_play_path", fake_play)
        await tts._synthesize_and_play("Hello, Rob.")

    asyncio.run(run())
    assert captured == {
        "text": "Hello, Rob.",
        "voice": "en-US-GuyNeural",
        "rate": "-5%",
        "pitch": "-8Hz",
        "volume": "+0%",
    }


def test_playback_start_callback_runs_after_synthesis_before_audio(monkeypatch):
    events = []

    class FakeCommunicate:
        def __init__(self, _text, **_kwargs):
            return None

        async def save(self, path):
            Path(path).write_bytes(b"mp3")
            events.append("synthesized")

    async def run():
        monkeypatch.setattr(
            text_to_speech,
            "edge_tts",
            SimpleNamespace(Communicate=FakeCommunicate),
        )
        tts = TextToSpeech(TTSConfig(playback_command="mpv"))

        async def playback_started():
            events.append("gesture")

        async def fake_play(_path, is_wav=False):
            assert not is_wav
            events.append("audio")

        monkeypatch.setattr(tts, "_play_path", fake_play)
        await tts._synthesize_and_play(
            "Hello, Rob.",
            on_playback_start=playback_started,
        )

    asyncio.run(run())

    assert events == ["synthesized", "gesture", "audio"]


def test_http_403_error_hides_signed_url_and_reports_clock_check():
    class Forbidden(Exception):
        status = 403

        def __str__(self):
            return "wss://example.invalid?Sec-MS-GEC=secret-token"

    detail = _synthesis_error_detail(Forbidden())

    assert "HTTP 403" in detail
    assert "UTC clock" in detail
    assert "secret-token" not in detail
