"""Tests for single-owner pan/tilt expression control."""

import asyncio
from types import SimpleNamespace

from r08y.config import ExpressionConfig, ServoConfig, TrackerConfig
from r08y.modules.expression_controller import ExpressionController, InteractionState
from r08y.modules.face_detector import FaceDetection
from r08y.modules.face_tracker import FaceTracker
from r08y.modules.servo_controller import MockServoController
from r08y.skills._bus import EventBus
from r08y.skills.camera_gestures import Skill as CameraGestureSkill


class RecordingServo(MockServoController):
    def __init__(self, config):
        super().__init__(config)
        self.history = []

    async def set_angle(self, channel, angle):
        await super().set_angle(channel, angle)
        self.history.append((channel, await self.get_angle(channel)))


async def _yield(_duration):
    await asyncio.sleep(0)


def test_gestures_are_smoothed_clamped_and_return_home():
    async def run():
        servo_config = ServoConfig(
            driver="mock",
            pan_min=80,
            pan_max=100,
            tilt_min=82,
            tilt_max=98,
            home_pan=90,
            home_tilt=90,
        )
        servo = RecordingServo(servo_config)
        expression = ExpressionController(
            servo,
            servo_config,
            ExpressionConfig(update_interval=0.02, gesture_scale=1.5),
            sleep=_yield,
        )
        await servo.start()
        await expression.start()

        assert await expression.perform_gesture("dance")
        pan, tilt = await expression.get_pose()
        assert round(pan, 6) == 90
        assert round(tilt, 6) == 90
        assert len(servo.history) > 10
        assert all(
            80 <= angle <= 100 if channel == servo_config.pan_channel else 82 <= angle <= 98
            for channel, angle in servo.history
        )
        previous = {
            servo_config.pan_channel: servo_config.home_pan,
            servo_config.tilt_channel: servo_config.home_tilt,
        }
        for channel, angle in servo.history:
            assert abs(angle - previous[channel]) <= 3.01
            previous[channel] = angle

        await expression.stop()
        await servo.stop()

    asyncio.run(run())


def test_queued_gestures_complete_in_order():
    async def run():
        servo_config = ServoConfig(driver="mock")
        servo = RecordingServo(servo_config)
        expression = ExpressionController(
            servo,
            servo_config,
            ExpressionConfig(update_interval=0.02),
            sleep=_yield,
        )
        await servo.start()
        await expression.start()

        first = asyncio.create_task(expression.perform_gesture("nod"))
        second = asyncio.create_task(expression.perform_gesture("shake"))
        assert await first
        first_history_length = len(servo.history)
        assert await second
        assert len(servo.history) > first_history_length

        await expression.stop()
        await servo.stop()

    asyncio.run(run())


def test_active_and_queued_motion_can_be_cancelled():
    async def run():
        servo_config = ServoConfig(driver="mock")
        servo = RecordingServo(servo_config)
        sleep_started = asyncio.Event()
        release_sleep = asyncio.Event()

        async def controlled_sleep(_duration):
            sleep_started.set()
            await release_sleep.wait()

        expression = ExpressionController(
            servo,
            servo_config,
            ExpressionConfig(update_interval=0.02),
            sleep=controlled_sleep,
        )
        await servo.start()
        await expression.start()

        active = asyncio.create_task(expression.perform_gesture("sweep"))
        await sleep_started.wait()
        queued = asyncio.create_task(expression.perform_gesture("nod"))
        await asyncio.sleep(0)
        await expression.cancel_motion()
        release_sleep.set()

        assert await active is False
        assert await queued is False
        await expression.stop()
        await servo.stop()

    asyncio.run(run())


def test_interaction_state_changes_ambient_target_without_large_step():
    async def run():
        servo_config = ServoConfig(driver="mock")
        servo = RecordingServo(servo_config)
        expression = ExpressionController(
            servo,
            servo_config,
            ExpressionConfig(
                update_interval=0.05,
                max_step_degrees=2,
                speaking_bob_degrees=4,
            ),
            clock=lambda: 0.3,
        )
        await servo.start()
        await expression.start()
        await expression.set_interaction_state(InteractionState.SPEAKING)
        before = await expression.get_pose()
        await expression._ambient_tick()
        after = await expression.get_pose()

        assert expression.interaction_state == InteractionState.SPEAKING
        assert after != before
        assert abs(after[0] - before[0]) <= 2
        assert abs(after[1] - before[1]) <= 2
        await expression.stop()
        await servo.stop()

    asyncio.run(run())


def test_face_tracker_submits_gaze_target_to_expression_layer():
    class FakeExpression:
        def __init__(self):
            self.target = (90.0, 90.0)
            self.updates = []

        async def get_gaze_target(self):
            return self.target

        async def set_gaze_target(self, pan, tilt):
            self.target = (pan, tilt)
            self.updates.append(self.target)

        async def home(self):
            self.target = (90.0, 90.0)

    async def run():
        expression = FakeExpression()
        tracker = FaceTracker(
            expression,
            ServoConfig(),
            TrackerConfig(pid_ki=0, pid_kd=0),
            640,
            480,
        )
        await tracker.update(FaceDetection(x=0, y=0, w=100, h=100, confidence=1))
        assert expression.updates
        assert expression.target != (90.0, 90.0)

    asyncio.run(run())


def test_camera_gesture_skill_submits_named_gesture():
    class FakeExpression:
        def __init__(self):
            self.gestures = []

        async def perform_gesture(self, name):
            self.gestures.append(name)
            return True

    async def run():
        expression = FakeExpression()
        skill = CameraGestureSkill()
        context = SimpleNamespace(expression=expression, event_bus=EventBus())
        await skill.setup(context)
        await skill._run_gesture("nod")
        assert expression.gestures == ["nod"]

    asyncio.run(run())
