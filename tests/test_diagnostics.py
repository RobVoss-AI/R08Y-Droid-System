import asyncio

from r08y.config import R08YConfig
from r08y.diagnostics import (
    run_audio_test,
    run_expression_test,
    run_interaction_test,
    run_speech_test,
    run_wake_word_test,
)


class FakeVoice:
    def __init__(self):
        self.available = False
        self.capture_detail = "alsa backend; capture passed"
        self.measured_chunks = None
        self.stopped = False

    async def start(self):
        self.available = True

    async def measure_level(self, chunks):
        self.measured_chunks = chunks
        return 123.0

    async def stop(self):
        self.stopped = True


class FakeTTS:
    def __init__(self):
        self.played = False
        self.stopped = False

    async def start(self):
        return None

    async def play_wav_bytes(self, _wav):
        self.played = True
        return True

    async def stop(self):
        self.stopped = True


def test_guided_audio_test_prompts_and_stops_sampling_automatically():
    voice = FakeVoice()
    tts = FakeTTS()
    messages = []

    results = asyncio.run(
        run_audio_test(
            R08YConfig(),
            voice_factory=lambda _audio, _wake: voice,
            tts_factory=lambda _config: tts,
            tone_factory=lambda _frequency, _duration: b"wav",
            emit=messages.append,
        )
    )

    assert messages == [
        "R08Y audio test starting. Do not speak until READY appears.",
        "READY: Say 'R08Y audio check' once. Sampling stops automatically after two seconds.",
        "Microphone sampling complete. You can stop talking.",
        "SPEAKER: A short tone will play now.",
    ]
    assert voice.measured_chunks == 63
    assert [(result.name, result.status) for result in results] == [
        ("microphone_capture", "pass"),
        ("speaker_command", "pass"),
    ]
    assert voice.stopped
    assert tts.played
    assert tts.stopped


class FakeSpeechTTS:
    available = False
    output_detail = "XVF3800 selected"
    last_error = None

    def __init__(self):
        self.spoken = None
        self.stopped = False

    async def start(self):
        self.available = True

    async def speak_and_wait(self, text):
        self.spoken = text
        return True

    async def stop(self):
        self.stopped = True


def test_speech_test_auditions_configured_voice_without_other_hardware():
    tts = FakeSpeechTTS()
    messages = []
    config = R08YConfig()

    results = asyncio.run(
        run_speech_test(
            config,
            tts_factory=lambda _config: tts,
            emit=messages.append,
        )
    )

    assert "voice=en-US-AndrewMultilingualNeural" in messages[0]
    assert "rate=+1%" in messages[0]
    assert "pitch=+2Hz" in messages[0]
    assert messages[1] == "R08Y will speak now."
    assert tts.spoken.startswith("Hi, Rob. I'm R08Y")
    assert [(result.name, result.status) for result in results] == [
        ("speech_playback", "pass")
    ]
    assert tts.stopped


def test_wake_word_test_requires_and_detects_custom_roby_model():
    class FakeWakeVoice:
        available = False
        wake_available = False
        wake_engine = "none"
        wake_detail = "not started"

        def __init__(self):
            self.stopped = False

        async def start(self):
            self.available = True
            self.wake_available = True
            self.wake_engine = "porcupine"
            self.wake_detail = "listening for 'ROBY'"

        async def wait_for_wake(self):
            return True

        async def stop(self):
            self.stopped = True

    voice = FakeWakeVoice()
    messages = []
    results = asyncio.run(
        run_wake_word_test(
            R08YConfig(),
            voice_factory=lambda _audio, _wake: voice,
            timeout_seconds=1,
            emit=messages.append,
        )
    )

    assert messages == [
        "R08Y wake-word test starting. This listens only for 'ROBY'.",
        "READY: Say 'ROBY' once within 1 second.",
    ]
    assert [(result.name, result.status) for result in results] == [
        ("wake_word", "pass")
    ]
    assert voice.stopped


def test_wake_word_test_reports_missing_custom_model_without_waiting():
    class FakeWakeVoice:
        available = True
        wake_available = False
        wake_engine = "none"
        wake_detail = "Custom ROBY wake-word model not found"

        async def start(self):
            return None

        async def stop(self):
            return None

    results = asyncio.run(
        run_wake_word_test(
            R08YConfig(),
            voice_factory=lambda _audio, _wake: FakeWakeVoice(),
            timeout_seconds=1,
            emit=lambda _message: None,
        )
    )

    assert [(result.name, result.status) for result in results] == [
        ("wake_word", "fail")
    ]
    assert "model not found" in results[0].detail


def test_expression_test_runs_named_sequence_without_mobility():
    class FakeServo:
        available = False

        def __init__(self):
            self.stopped = False

        async def start(self):
            self.available = True

        async def stop(self):
            self.stopped = True
            self.available = False

    class FakeExpression:
        available = False

        def __init__(self, servo, _servo_config, _expression_config):
            self.servo = servo
            self.gestures = []
            self.home_count = 0
            self.stopped = False

        async def start(self):
            self.available = True

        async def perform_gesture(self, name):
            self.gestures.append(name)
            return True

        async def home(self):
            self.home_count += 1
            return True

        async def stop(self):
            self.stopped = True
            self.available = False

    servo = FakeServo()
    holder = {}
    messages = []

    def expression_factory(*args):
        holder["expression"] = FakeExpression(*args)
        return holder["expression"]

    results = asyncio.run(
        run_expression_test(
            R08YConfig(),
            servo_factory=lambda _config: servo,
            expression_factory=expression_factory,
            emit=messages.append,
        )
    )

    expression = holder["expression"]
    assert "wheels will not move" in messages[0]
    assert expression.gestures == [
        "acknowledge", "nod", "shake", "curious", "pleased", "uncertain", "greet"
    ]
    assert expression.home_count == 2
    assert expression.stopped
    assert servo.stopped
    assert [(result.name, result.status) for result in results] == [
        ("expression_sequence", "pass")
    ]


def test_interaction_test_coordinates_cue_speech_and_expression_without_mobility():
    class FakeServo:
        available = False

        async def start(self):
            self.available = True

        async def stop(self):
            self.available = False

    class FakeExpression:
        available = False

        def __init__(self, *_args):
            self.states = []
            self.home_count = 0
            self.stopped = False

        async def start(self):
            self.available = True

        async def set_interaction_state(self, state):
            self.states.append(state.value)

        async def home(self):
            self.home_count += 1
            return True

        async def stop(self):
            self.stopped = True

    class FakeTTS:
        available = False
        output_detail = "XVF3800 selected"
        last_error = None

        def __init__(self):
            self.lines = []
            self.stopped = False

        async def start(self):
            self.available = True

        async def speak_and_wait(self, line, *, on_playback_start=None):
            if on_playback_start is not None:
                await on_playback_start()
            self.lines.append(line)
            return True

        async def stop(self):
            self.stopped = True

    class FakeInteraction:
        def __init__(self, _config):
            self.acknowledged = False
            self.responses = []

        async def acknowledge(self, _tts, _expression):
            self.acknowledged = True
            return True

        async def queue_response_gesture(self, line, _expression):
            self.responses.append(line)
            return "greet"

    servo = FakeServo()
    expression = FakeExpression()
    tts = FakeTTS()
    interaction = FakeInteraction(None)
    messages = []

    results = asyncio.run(
        run_interaction_test(
            R08YConfig(),
            servo_factory=lambda _config: servo,
            expression_factory=lambda *_args: expression,
            tts_factory=lambda _config: tts,
            interaction_factory=lambda _config: interaction,
            emit=messages.append,
        )
    )

    assert "wheels will not move" in messages[0]
    assert interaction.acknowledged
    assert interaction.responses == tts.lines
    assert len(tts.lines) == 3
    assert expression.states == ["speaking", "speaking", "speaking", "idle"]
    assert expression.home_count == 2
    assert expression.stopped
    assert tts.stopped
    assert [(result.name, result.status) for result in results] == [
        ("interaction_audition", "pass")
    ]


def test_interaction_test_stops_after_first_synthesis_failure():
    class FakeServo:
        available = False

        async def start(self):
            self.available = True

        async def stop(self):
            self.available = False

    class FakeExpression:
        available = False

        async def start(self):
            self.available = True

        async def home(self):
            return True

        async def set_interaction_state(self, _state):
            return None

        async def stop(self):
            self.available = False

    class FakeTTS:
        available = False
        output_detail = "XVF3800 selected"
        last_error = "Edge TTS rejected synthesis with HTTP 403."

        def __init__(self):
            self.attempts = 0

        async def start(self):
            self.available = True

        async def speak_and_wait(self, _line, *, on_playback_start=None):
            self.attempts += 1
            return False

        async def stop(self):
            self.available = False

    class FakeInteraction:
        async def acknowledge(self, _tts, _expression):
            return True

        async def queue_response_gesture(self, _line, _expression):
            return None

    tts = FakeTTS()
    messages = []
    results = asyncio.run(
        run_interaction_test(
            R08YConfig(),
            servo_factory=lambda _config: FakeServo(),
            expression_factory=lambda *_args: FakeExpression(),
            tts_factory=lambda _config: tts,
            interaction_factory=lambda _config: FakeInteraction(),
            emit=messages.append,
        )
    )

    assert tts.attempts == 1
    assert any(message.startswith("STOP:") for message in messages)
    assert results[0].status == "fail"
    assert "HTTP 403" in results[0].detail
