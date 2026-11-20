"""Loudness envelope of R08Y's own voice for speech-synchronized head motion.

Reachy Mini moves its head with the loudness of the audio it is playing. R08Y
does the same with pan/tilt: TextToSpeech measures each reply before playback
and the expression controller follows the envelope while the reply plays.
"""

from __future__ import annotations

import io
import math
import wave
from typing import List, Tuple

import numpy as np

FRAME_SECONDS = 0.05
# Loudness range mapped onto the motion range, as in Reachy Mini's wobbler.
FLOOR_DBFS = -46.0
CEILING_DBFS = -18.0
# Motion eases out between syllables over about this many seconds.
RELEASE_SECONDS = 0.25


def speech_envelope(
    wav_data: bytes, frame_seconds: float = FRAME_SECONDS
) -> Tuple[List[float], float]:
    """Return one 0-1 loudness value per frame of 16-bit PCM WAV audio.

    Unsupported or unreadable audio returns an empty envelope, which leaves the
    controller's default speaking motion in place.
    """
    try:
        with wave.open(io.BytesIO(wav_data), "rb") as wav_file:
            if wav_file.getsampwidth() != 2:
                return [], frame_seconds
            channels = wav_file.getnchannels()
            rate = wav_file.getframerate()
            raw = wav_file.readframes(wav_file.getnframes())
    except (wave.Error, EOFError):
        return [], frame_seconds

    samples = np.frombuffer(raw[: len(raw) - len(raw) % 2], dtype="<i2")
    if channels > 1:
        samples = samples[: len(samples) - len(samples) % channels]
        samples = samples.reshape(-1, channels).mean(axis=1)
    frame_length = max(1, int(rate * frame_seconds))
    usable = len(samples) - len(samples) % frame_length
    if usable == 0:
        return [], frame_seconds

    frames = samples[:usable].astype(np.float64).reshape(-1, frame_length)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    with np.errstate(divide="ignore"):
        dbfs = 20.0 * np.log10(rms / 32768.0)
    raw_levels = np.clip(
        (dbfs - FLOOR_DBFS) / (CEILING_DBFS - FLOOR_DBFS), 0.0, 1.0
    )

    release = math.exp(-frame_seconds / RELEASE_SECONDS)
    levels: List[float] = []
    previous = 0.0
    for level in raw_levels:
        level = float(level)
        previous = level if level >= previous else previous * release + level * (1 - release)
        levels.append(round(previous, 3))
    return levels, frame_seconds
