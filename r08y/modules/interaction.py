"""Conversation cues and response choreography for R08Y."""

from __future__ import annotations

import asyncio
import io
import math
import re
import struct
import wave
from typing import Optional

from r08y.config import InteractionConfig


_GREETING = re.compile(r"^(hi|hello|hey|good (morning|afternoon|evening))\b")
_AFFIRMATIVE = re.compile(r"^(yes|yep|yeah|absolutely|correct|exactly)\b")
_NEGATIVE = re.compile(r"^(no|nope|not yet|I won['’]t|I can['’]t)\b", re.IGNORECASE)


def _has_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def generate_acknowledgement_cue(gain: float = 0.14) -> bytes:
    """Create a quiet rising two-note cue with short attack and release ramps."""
    sample_rate = 16000
    parts = ((659.25, 0.055), (0.0, 0.018), (880.0, 0.070))
    fade_samples = int(sample_rate * 0.008)
    samples: list[int] = []

    for frequency, duration in parts:
        count = max(1, int(sample_rate * duration))
        for index in range(count):
            if frequency == 0:
                value = 0.0
            else:
                envelope = min(
                    1.0,
                    index / max(1, fade_samples),
                    (count - 1 - index) / max(1, fade_samples),
                )
                value = (
                    32767
                    * gain
                    * envelope
                    * math.sin(2 * math.pi * frequency * index / sample_rate)
                )
            samples.append(int(value))

    raw = struct.pack(f"<{len(samples)}h", *samples)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(raw)
    return output.getvalue()


class InteractionDirector:
    """Coordinate restrained audio and motion cues around a conversation turn."""

    def __init__(self, config: InteractionConfig):
        self._config = config

    def response_gesture(self, text: str) -> Optional[str]:
        """Choose one conservative gesture from the spoken response."""
        if not self._config.enabled or not self._config.response_gestures:
            return None

        normalized = " ".join(text.strip().casefold().replace("’", "'").split())
        if not normalized:
            return None

        if _GREETING.match(normalized):
            return "greet"
        if _has_phrase(
            normalized,
            (
                "i'm sorry",
                "i am sorry",
                "i don't know",
                "i do not know",
                "not sure",
                "having trouble",
                "can't reach",
                "cannot reach",
                "went wrong",
            ),
        ):
            return "uncertain"
        if _NEGATIVE.match(normalized) and not normalized.startswith("no problem"):
            return "shake"
        if normalized.endswith("?"):
            return "curious"
        if _AFFIRMATIVE.match(normalized):
            return "nod"
        if _has_phrase(
            normalized,
            (
                "got it",
                "all set",
                "ready when you are",
                "nice work",
                "well done",
                "glad to",
                "thank you",
                "thanks",
            ),
        ):
            return "pleased"
        return None

    async def acknowledge(self, tts, expression) -> bool:
        """Signal that the wake word was heard before microphone capture begins."""
        if not self._config.enabled:
            return False

        actions = []
        if self._config.acknowledgement_gesture:
            actions.append(
                expression.perform_gesture(
                    "acknowledge",
                    wait=False,
                    priority=5,
                )
            )
        if self._config.acknowledgement_cue:
            actions.append(
                tts.play_wav_bytes(
                    generate_acknowledgement_cue(self._config.cue_gain)
                )
            )
        if not actions:
            return False
        results = await asyncio.gather(*actions, return_exceptions=True)
        return all(result is True for result in results)

    async def queue_response_gesture(self, text: str, expression) -> Optional[str]:
        """Queue at most one gesture for a spoken response."""
        gesture = self.response_gesture(text)
        if gesture is None:
            return None
        accepted = await expression.perform_gesture(
            gesture,
            wait=False,
            priority=8,
        )
        return gesture if accepted else None
