import asyncio

from r08y.config import R08YConfig
from r08y.diagnostics import run_audio_test, run_expression_test, run_speech_test


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
