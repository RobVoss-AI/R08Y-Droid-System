"""Tests for speech-synchronized head motion."""

import asyncio
import io
import math
import struct
import wave

from r08y.config import ExpressionConfig, ServoConfig
from r08y.modules.expression_controller import ExpressionController, InteractionState
from r08y.modules.servo_controller import MockServoController
from r08y.modules.speech_motion import speech_envelope


def _wav(samples, rate=16000, channels=1):
    output = io.BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)
        wav_file.setframerate(rate)
        wav_file.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return output.getvalue()


def test_envelope_follows_loudness_and_eases_out():
    rate = 16000
    loud = [int(12000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(rate // 5)]
    silence = [0] * (rate // 5)

    levels, frame_seconds = speech_envelope(_wav(silence + loud + silence, rate))

    assert frame_seconds == 0.05
    assert len(levels) == 12
    assert levels[:4] == [0.0] * 4
    assert min(levels[4:8]) > 0.8
    # Release decays over several frames instead of snapping to zero.
    assert 0 < levels[8] < levels[7]
    assert levels[-1] < levels[8]


def test_unreadable_audio_has_no_envelope():
    assert speech_envelope(b"not a wav") == ([], 0.05)


def test_speaking_motion_follows_the_envelope():
    async def run():
        now = [100.0]
        servo_config = ServoConfig(driver="mock")
        servo = MockServoController(servo_config)
        await servo.start()
        expression = ExpressionController(
            servo,
            servo_config,
            ExpressionConfig(speaking_bob_degrees=2.0),
            clock=lambda: now[0],
        )
        await expression.set_interaction_state(InteractionState.SPEAKING)
        await expression.set_speech_envelope([0.0, 1.0], 0.5)

        now[0] = 100.1  # silent frame: no voice-driven motion
        assert expression._ambient_target() == (90.0, 90.0)
        # Loud frame: the head nods and sways around a slightly lifted pose,
        # never more than twice speaking_bob_degrees plus the lift.
        poses = []
        for step in range(50):
            now[0] = 100.5 + step * 0.01
            poses.append(expression._ambient_target())
        tilts = [tilt for _pan, tilt in poses]
        assert sum(tilts) / len(tilts) < 90.0
        assert max(abs(tilt - 90.0) for tilt in tilts) <= 6.0
        assert max(abs(pan - 90.0) for pan, _tilt in poses) > 0.5

        await expression.set_interaction_state(InteractionState.IDLE)
        assert expression._speech_level(now[0]) is None

    asyncio.run(run())
