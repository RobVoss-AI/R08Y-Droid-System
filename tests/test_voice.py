"""Tests for the voice input module."""

import struct

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


if __name__ == "__main__":
    test_rms_computation()
    test_rms_moderate_signal()
    print("All voice tests passed!")
