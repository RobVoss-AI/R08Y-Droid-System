import asyncio
import io
import wave

from r08y.config import InteractionConfig
from r08y.modules.interaction import (
    InteractionDirector,
    generate_acknowledgement_cue,
)


class FakeExpression:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []

    async def perform_gesture(self, name, **kwargs):
        self.calls.append((name, kwargs))
        return self.accepted


class FakeTTS:
    def __init__(self, played=True):
        self.played = played
        self.wav = None

    async def play_wav_bytes(self, wav):
        self.wav = wav
        return self.played


def test_acknowledgement_cue_is_quiet_native_rate_mono_wav():
    data = generate_acknowledgement_cue(0.14)

    with wave.open(io.BytesIO(data), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getsampwidth() == 2
        assert 0.13 < wav.getnframes() / wav.getframerate() < 0.16


def test_acknowledgement_coordinates_audio_and_bounded_gesture():
    director = InteractionDirector(InteractionConfig())
    tts = FakeTTS()
    expression = FakeExpression()

    completed = asyncio.run(director.acknowledge(tts, expression))

    assert completed
    assert tts.wav.startswith(b"RIFF")
    assert expression.calls == [
        ("acknowledge", {"wait": False, "priority": 5})
    ]


def test_response_gesture_uses_meaning_and_skips_neutral_replies():
    director = InteractionDirector(InteractionConfig())

    assert director.response_gesture("Hi, Rob. I'm ready.") == "greet"
    assert director.response_gesture("I'm sorry, I didn't catch that.") == "uncertain"
    assert director.response_gesture("’I'm sorry’ isn't enough.") == "uncertain"
    assert director.response_gesture("No, I can't do that safely.") == "shake"
    assert director.response_gesture("Would you like me to try again?") == "curious"
    assert director.response_gesture("Yes, that's correct.") == "nod"
    assert director.response_gesture("Got it. We'll do that next.") == "pleased"
    assert director.response_gesture("The temperature is seventy degrees.") is None


def test_response_gesture_does_not_shake_for_no_problem():
    director = InteractionDirector(InteractionConfig())

    assert director.response_gesture("No problem. I can help with that.") is None


def test_interaction_features_can_be_disabled_independently():
    director = InteractionDirector(
        InteractionConfig(
            acknowledgement_cue=False,
            acknowledgement_gesture=False,
            response_gestures=False,
        )
    )
    tts = FakeTTS()
    expression = FakeExpression()

    completed = asyncio.run(director.acknowledge(tts, expression))

    assert not completed
    assert tts.wav is None
    assert expression.calls == []
    assert director.response_gesture("Yes.") is None


def test_queue_response_gesture_reports_rejected_motion():
    director = InteractionDirector(InteractionConfig())
    expression = FakeExpression(accepted=False)

    gesture = asyncio.run(
        director.queue_response_gesture("Would you like another try?", expression)
    )

    assert gesture is None
    assert expression.calls == [
        ("curious", {"wait": False, "priority": 8})
    ]
