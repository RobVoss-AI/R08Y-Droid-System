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

        async def think(self, _text, _visual, _user_name, **_kwargs):
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


class _Harness:
    """Scripted voice, brain, and speaker for multi-turn pipeline tests."""

    def __init__(self, *, recordings, replies, follow_up_seconds=6.0, tools=None):
        self.events = []
        self.shutdown = asyncio.Event()
        self.recordings = list(recordings)
        self.replies = list(replies)
        self.follow_up_seconds = follow_up_seconds
        self.tools = tools
        self.wakes = 1

    def run(self):
        harness = self

        class Voice:
            available = True
            wake_available = True
            wake_detail = "ROBY armed"
            wake_phrase = "ROBY"

            async def wait_for_wake(self):
                harness.events.append("wake")
                if harness.wakes == 0:
                    return False
                harness.wakes -= 1
                return True

            async def record_speech(self, start_timeout=None):
                harness.events.append(("record", start_timeout))
                return harness.recordings.pop(0) if harness.recordings else None

        class STT:
            async def transcribe(self, audio):
                return audio.decode()

        class Brain:
            def check_special_command(self, _text):
                return None

            async def think(self, text, _visual, _user, *, tools=None, on_interim_text=None):
                harness.events.append(("think", text))
                reply = harness.replies.pop(0)
                if isinstance(reply, tuple):
                    interim, reply = reply
                    await on_interim_text(interim)
                return reply

        class TTS:
            def interrupt(self):
                pass

            async def speak_and_wait(self, text, *, on_playback_start=None):
                if on_playback_start is not None:
                    await on_playback_start()
                harness.events.append(("say", text))
                return True

        class Expression:
            async def set_interaction_state(self, _state):
                pass

            async def cancel_motion(self):
                pass

        class Interaction:
            follow_up_seconds = harness.follow_up_seconds
            barge_in = False

            async def acknowledge(self, _tts, _expression):
                harness.events.append("cue")
                return True

            async def queue_response_gesture(self, text, _expression):
                harness.events.append(("gesture", text))

        asyncio.run(
            voice_pipeline(
                Voice(),
                STT(),
                Brain(),
                TTS(),
                face_recognizer=object(),
                camera=object(),
                expression=Expression(),
                interaction=Interaction(),
                state=DroidState(),
                shutdown=self.shutdown,
                tools=self.tools,
            )
        )
        return self.events


def test_follow_up_turn_continues_without_wake_word():
    events = _Harness(
        recordings=[b"what's up", b"tell me more"],
        replies=["Not much.", "That's all I know."],
    ).run()

    assert events.count("cue") == 1
    assert [event for event in events if event[0] == "record"] == [
        ("record", None),
        ("record", 6.0),
        ("record", 6.0),
    ]
    assert ("think", "tell me more") in events
    # The quiet third window falls back to waiting for ROBY.
    assert events[-1] == "wake"


def test_follow_up_disabled_returns_to_wake_word():
    events = _Harness(
        recordings=[b"hello"], replies=["Hi."], follow_up_seconds=0.0
    ).run()

    assert [event for event in events if event[0] == "record"] == [("record", None)]


def test_words_before_an_action_play_before_the_answer_without_a_gesture():
    events = _Harness(
        recordings=[b"what do you see"],
        replies=[("Let me look.", "A red mug.")],
        follow_up_seconds=0.0,
    ).run()

    assert events.index(("say", "Let me look.")) < events.index(("say", "A red mug."))
    assert ("gesture", "Let me look.") not in events
    assert ("gesture", "A red mug.") in events


def test_gesture_only_answer_is_silent_and_end_conversation_skips_follow_up():
    class Tools:
        expressed = True
        conversation_ended = True

    events = _Harness(
        recordings=[b"bye"], replies=[""], tools=Tools()
    ).run()

    assert not [event for event in events if event[0] == "say"]
    assert [event for event in events if event[0] == "record"] == [("record", None)]
