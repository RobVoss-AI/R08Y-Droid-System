"""Tests for the voice input module."""

import asyncio
import struct
from types import SimpleNamespace

from r08y.config import AudioConfig, WakeWordConfig
from r08y.modules import voice_input as voice_input_module
from r08y.modules.audio_devices import AlsaCaptureDevice
from r08y.modules.voice_input import VoiceInput


class FakeStream:
    def __init__(self, error=None):
        self.error = error
        self.active = True
        self.read_sizes = []

    def is_active(self):
        return self.active

    def read(self, frames, exception_on_overflow=False):
        self.read_sizes.append(frames)
        if self.error:
            raise self.error
        return struct.pack(f"<{frames * 2}h", *([1000, 3000] * frames))

    def stop_stream(self):
        self.active = False

    def close(self):
        self.active = False


class MonoFakeStream(FakeStream):
    def read(self, frames, exception_on_overflow=False):
        self.read_sizes.append(frames)
        if self.error:
            raise self.error
        return struct.pack(f"<{frames}h", *([2000] * frames))


class FakePyAudio:
    def __init__(self, stream):
        self.stream = stream
        self.open_calls = []
        self.terminated = False
        self.devices = [
            {
                "index": 0,
                "name": "Built-in Microphone",
                "maxInputChannels": 1,
                "defaultSampleRate": 48000,
            },
            {
                "index": 3,
                "name": "reSpeaker XVF3800 4-Mic Array",
                "maxInputChannels": 2,
                "defaultSampleRate": 16000,
            },
        ]

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def get_default_input_device_info(self):
        return self.devices[0]

    def open(self, **kwargs):
        self.open_calls.append(kwargs)
        return self.stream

    def terminate(self):
        self.terminated = True


class NativeFallbackPyAudio(FakePyAudio):
    def open(self, **kwargs):
        self.open_calls.append(kwargs)
        if kwargs["rate"] != 48000 or kwargs["channels"] != 1:
            raise ValueError("unsupported format")
        return self.stream


def _install_fake_pyaudio(monkeypatch, stream, fake=None):
    fake = fake or FakePyAudio(stream)
    module = SimpleNamespace(paInt16=8, PyAudio=lambda: fake)
    monkeypatch.setattr(voice_input_module, "pyaudio", module, raising=False)
    monkeypatch.setattr(voice_input_module, "PYAUDIO_AVAILABLE", True)
    monkeypatch.setattr(
        voice_input_module,
        "AlsaResolver",
        lambda: SimpleNamespace(resolve=lambda _name: None),
    )
    return fake


def test_rms_computation():
    """Test RMS energy calculation for silence and noise."""
    # Silence (all zeros)
    silence = struct.pack("<512h", *([0] * 512))
    rms = VoiceInput._compute_rms(silence)
    assert rms == 0.0

    # Loud signal (max amplitude)
    loud = struct.pack("<512h", *([32767] * 512))
    rms = VoiceInput._compute_rms(loud)
    assert rms > 30000

    # Empty
    rms = VoiceInput._compute_rms(b"")
    assert rms == 0.0


def test_rms_moderate_signal():
    """Test RMS for a moderate signal level."""
    # Half amplitude
    moderate = struct.pack("<512h", *([16000] * 512))
    rms = VoiceInput._compute_rms(moderate)
    assert 15000 < rms < 17000


def test_stereo_48k_is_normalized_to_mono_16k():
    voice = VoiceInput(AudioConfig(sample_rate=16000, channels=1, chunk_size=4), WakeWordConfig())
    voice._input_rate = 48000
    voice._input_channels = 2
    # Four output samples after downmix + 3:1 resampling.
    stereo = struct.pack("<24h", *([1000, 3000] * 12))
    normalized = voice._normalize_audio(stereo)
    samples = struct.unpack("<4h", normalized)
    assert samples == (2000, 2000, 2000, 2000)


def test_startup_uses_xvf3800_native_capture_and_downmixes(monkeypatch):
    stream = FakeStream()
    fake = _install_fake_pyaudio(monkeypatch, stream)
    voice = VoiceInput(AudioConfig(), WakeWordConfig(engine="rms"))

    async def run():
        await voice.start()
        assert voice.available
        assert voice.capture_status == "pass"
        assert "S16_LE 16000 Hz/2 ch" in voice.capture_detail
        assert fake.open_calls == [
            {
                "format": 8,
                "channels": 2,
                "rate": 16000,
                "input": True,
                "input_device_index": 3,
                "frames_per_buffer": 512,
            }
        ]
        assert stream.read_sizes == [512]
        await voice.stop()

    asyncio.run(run())


def test_xvf3800_auto_backend_uses_name_resolved_bounded_alsa(monkeypatch):
    stream = FakeStream()
    device = AlsaCaptureDevice(
        card=7,
        device=0,
        card_id="XVF3800",
        card_name="reSpeaker XVF3800 4-Mic Array",
        device_name="USB Audio",
    )
    monkeypatch.setattr(
        voice_input_module,
        "AlsaResolver",
        lambda: SimpleNamespace(resolve=lambda _name: device),
    )
    calls = []

    def create_stream(hardware_id, sample_rate, channels):
        calls.append((hardware_id, sample_rate, channels))
        return stream

    monkeypatch.setattr(voice_input_module, "AlsaCaptureStream", create_stream)
    monkeypatch.setattr(voice_input_module, "PYAUDIO_AVAILABLE", False)
    voice = VoiceInput(AudioConfig(), WakeWordConfig(engine="rms"))

    async def run():
        await voice.start()
        assert voice.available
        assert voice.capture_backend == "alsa"
        assert voice.alsa_hardware_id == "hw:7,0"
        assert calls == [("hw:7,0", 16000, 2)]
        assert "alsa backend" in voice.capture_detail
        await voice.stop()

    asyncio.run(run())


def test_startup_io_error_reports_usb_reseat_condition(monkeypatch):
    stream = FakeStream(OSError(5, "Input/output error"))
    fake = _install_fake_pyaudio(monkeypatch, stream)
    voice = VoiceInput(AudioConfig(), WakeWordConfig(engine="rms"))

    async def run():
        await voice.start()
        assert not voice.available
        assert voice.capture_status == "fail"
        assert voice.requires_usb_reseat
        assert "Physically unplug the XVF3800 USB cable" in voice.capture_detail
        assert "wait 10 seconds" in voice.capture_detail
        assert fake.terminated
        await voice.stop()

    asyncio.run(run())


def test_other_usb_microphone_falls_back_to_its_native_format(monkeypatch):
    stream = MonoFakeStream()
    fake = NativeFallbackPyAudio(stream)
    _install_fake_pyaudio(monkeypatch, stream, fake=fake)
    voice = VoiceInput(
        AudioConfig(device_name="Built-in Microphone"),
        WakeWordConfig(engine="rms"),
    )

    async def run():
        await voice.start()
        assert voice.available
        assert not voice.requires_usb_reseat
        assert [(call["rate"], call["channels"]) for call in fake.open_calls] == [
            (16000, 2),
            (48000, 1),
        ]
        assert "S16_LE 48000 Hz/1 ch" in voice.capture_detail
        await voice.stop()

    asyncio.run(run())


def test_voice_uses_name_discovered_alsa_id_as_portaudio_alias(monkeypatch):
    stream = FakeStream()
    fake = FakePyAudio(stream)
    fake.devices[1]["name"] = "USB Audio (hw:7,0)"
    _install_fake_pyaudio(monkeypatch, stream, fake=fake)
    monkeypatch.setattr(
        voice_input_module,
        "AlsaResolver",
        lambda: SimpleNamespace(
            resolve=lambda _name: SimpleNamespace(hardware_id="hw:7,0")
        ),
    )
    voice = VoiceInput(
        AudioConfig(capture_backend="pyaudio"),
        WakeWordConfig(engine="rms"),
    )

    async def run():
        await voice.start()
        assert voice.available
        assert voice.alsa_hardware_id == "hw:7,0"
        assert fake.open_calls[0]["input_device_index"] == 3
        await voice.stop()

    asyncio.run(run())


def test_xvf3800_alsa_timeout_reports_usb_reseat_and_closes(monkeypatch):
    stream = FakeStream(TimeoutError("ALSA capture timed out without audio data"))
    device = AlsaCaptureDevice(3, 0, "XVF3800", "reSpeaker XVF3800", "USB Audio")
    monkeypatch.setattr(
        voice_input_module,
        "AlsaResolver",
        lambda: SimpleNamespace(resolve=lambda _name: device),
    )
    monkeypatch.setattr(
        voice_input_module,
        "AlsaCaptureStream",
        lambda _hardware_id, _rate, _channels: stream,
    )
    voice = VoiceInput(AudioConfig(), WakeWordConfig(engine="rms"))

    async def run():
        await voice.start()
        assert not voice.available
        assert voice.requires_usb_reseat
        assert voice.capture_status == "fail"
        assert not stream.active
        await voice.stop()

    asyncio.run(run())


if __name__ == "__main__":
    test_rms_computation()
    test_rms_moderate_signal()
    print("All voice tests passed!")
