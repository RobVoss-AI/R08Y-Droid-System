import asyncio

from r08y.main import DroidState, voice_pipeline


def test_voice_turn_acknowledges_before_capture_and_gestures_at_playback():
    events = []

    class FakeVoice:
        available = True
        wake_available = True
        wake_detail = "ROBY armed"
        wake_phrase = "ROBY"

        async def wait_for_wake(self):
            events.append("wake")
            return True

        async def record_speech(self):
            events.append("record")
            return b"audio"

    class FakeSTT:
        async def transcribe(self, _audio):
            events.append("transcribe")
            return "Hello, Roby"

    class FakeBrain:
        def check_special_command(self, _text):
            return None

        async def think(self, _text, _visual, _user_name):
            events.append("think")
            return "Hi, Rob. I'm here and ready."

    class FakeTTS:
        def interrupt(self):
            events.append("interrupt")

        async def speak_and_wait(self, text, *, on_playback_start=None):
            events.append("synthesized")
            if on_playback_start is not None:
                await on_playback_start()
            events.append(("audio", text))
            shutdown.set()
            return True

    class FakeExpression:
        def __init__(self):
            self.states = []

        async def set_interaction_state(self, state):
            self.states.append(state.value)

        async def cancel_motion(self):
            events.append("cancel_motion")

    class FakeInteraction:
        async def acknowledge(self, _tts, _expression):
            events.append("acknowledge")
            return True

        async def queue_response_gesture(self, text, _expression):
            events.append(("gesture", text))
            return "greet"

    shutdown = asyncio.Event()
    state = DroidState()
    expression = FakeExpression()

    asyncio.run(
        voice_pipeline(
            FakeVoice(),
            FakeSTT(),
            FakeBrain(),
            FakeTTS(),
            face_recognizer=object(),
            camera=object(),
            expression=expression,
            interaction=FakeInteraction(),
            state=state,
            shutdown=shutdown,
        )
    )

    assert events.index("acknowledge") < events.index("record")
    assert events.index("synthesized") < events.index(
        ("gesture", "Hi, Rob. I'm here and ready.")
    )
    assert events.index(("gesture", "Hi, Rob. I'm here and ready.")) < events.index(
        ("audio", "Hi, Rob. I'm here and ready.")
    )
    assert expression.states == ["idle", "listening", "thinking", "speaking", "idle"]
    assert not state.is_listening
    assert not state.is_speaking
