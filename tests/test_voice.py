"""Tests for the voice input module."""

import struct

from r08y.config import AudioConfig, WakeWordConfig
from r08y.modules.voice_input import VoiceInput


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


if __name__ == "__main__":
    test_rms_computation()
    test_rms_moderate_signal()
    print("All voice tests passed!")
