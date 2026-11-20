"""Tests for the actions Claude can take through RobotTools."""

import asyncio
import base64
from types import SimpleNamespace

import numpy as np

from r08y.config import AIConfig, ExpressionConfig, MobilityConfig, ServoConfig
from r08y.modules.expression_controller import ExpressionController
from r08y.modules.mobility import MockMobilityController
from r08y.modules.power import BatteryReading, MockBatteryMonitor
from r08y.modules.robot_tools import RobotTools
from r08y.modules.sensors import MockDistanceSensor
from r08y.modules.servo_controller import MockServoController
from r08y.skills._registry import CommandRegistry


async def _yield(_duration):
    await asyncio.sleep(0)


async def _expression():
    servo_config = ServoConfig(driver="mock")
    servo = MockServoController(servo_config)
    await servo.start()
    expression = ExpressionController(
        servo, servo_config, ExpressionConfig(update_interval=0.01), sleep=_yield
    )
    await expression.start()
    return expression


class FakeTracker:
    def __init__(self):
        self.held = None
        self.enabled = True

    def hold(self, seconds):
        self.held = seconds

    def set_enabled(self, enabled):
        self.enabled = enabled


class FakeCamera:
    available = True
    frame_age = 0.1

    async def get_frame(self):
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        frame[:, :, 2] = 200
        return frame


def test_only_available_hardware_becomes_a_tool():
    tools = RobotTools(AIConfig())

    assert tools.names == ["end_conversation"]


def test_driving_requires_opt_in_and_live_mobility():
    async def run():
        mobility = MockMobilityController(MobilityConfig())
        await mobility.start()
        assert "drive" not in RobotTools(AIConfig(), mobility=mobility).names
        assert "drive" in RobotTools(AIConfig(allow_driving=True), mobility=mobility).names

    asyncio.run(run())


def test_express_and_move_head_use_the_expression_queue_and_hold_tracking():
    async def run():
        expression = await _expression()
        tracker = FakeTracker()
        tools = RobotTools(AIConfig(), expression=expression, face_tracker=tracker)
        tools.begin_turn()

        result = await tools.execute("express", {"emotion": "happy"})
        assert not result.is_error
        assert tools.expressed

        result = await tools.execute("move_head", {"direction": "left"})
        assert result.content.startswith("Looking left")
        assert tracker.held == 5.0
        pan, _tilt = await expression.get_pose()
        assert pan < 90

        bad = await tools.execute("express", {"emotion": "furious"})
        assert bad.is_error

        await tools.execute("set_face_tracking", {"enabled": False})
        assert tracker.enabled is False
        await expression.stop()

    asyncio.run(run())


def test_camera_photo_is_returned_as_an_image_block():
    async def run():
        tools = RobotTools(AIConfig(), camera=FakeCamera())
        result = await tools.execute("look_with_camera", {"question": "What color?"})
        image, note = result.content
        assert image["type"] == "image"
        assert image["source"]["media_type"] == "image/jpeg"
        assert base64.b64decode(image["source"]["data"])[:2] == b"\xff\xd8"
        assert "What color?" in note["text"]

    asyncio.run(run())


def test_drive_is_capped_and_forward_motion_respects_the_distance_guard():
    async def run():
        config = MobilityConfig(enabled=True, max_duration=0.3, default_speed=0.4)
        near = MockDistanceSensor(12.0)
        await near.start()
        mobility = MockMobilityController(config, near)
        await mobility.start()
        tools = RobotTools(AIConfig(allow_driving=True), config, mobility=mobility)

        turned = await tools.execute("drive", {"direction": "turn_left", "seconds": 9})
        assert turned.content == "Moved turn left for 0.3 seconds."
        assert mobility.history[0][:3] == (0.0, 0.0, 0.4)

        blocked = await tools.execute("drive", {"direction": "forward", "seconds": 1})
        assert blocked.is_error
        assert "blocked" in blocked.content

    asyncio.run(run())


def test_read_sensors_reports_distance_and_battery():
    async def run():
        distance = MockDistanceSensor(42.0)
        await distance.start()
        battery = MockBatteryMonitor(BatteryReading(volts=3.9, current_ma=-450.0, percent=75.0))
        tools = RobotTools(AIConfig(), distance=distance, battery=battery)

        result = await tools.execute("read_sensors", {})
        assert "Distance ahead: 42 cm." in result.content
        assert "Battery: 75% (3.90 V, on battery)." in result.content

    asyncio.run(run())


def test_skills_are_reachable_but_wheel_skills_stay_locked():
    async def run():
        registry = CommandRegistry()

        async def weather(text, user_name):
            return f"Sunny for {user_name}."

        async def forward(text, user_name):
            raise AssertionError("driving skill must stay locked")

        registry.register(["weather"], weather, "weather")
        registry.register(["move forward"], forward, "mobility_control")
        skills = SimpleNamespace(
            command_registry=registry, match_command=registry.match
        )
        tools = RobotTools(AIConfig(), skill_manager=skills)

        description = next(
            tool["description"] for tool in tools.definitions() if tool["name"] == "use_skill"
        )
        assert "weather" in description
        assert "move forward" not in description
        result = await tools.execute("use_skill", {"phrase": "weather today"}, user_name="Rob")
        assert result.content == "Sunny for Rob."
        locked = await tools.execute("use_skill", {"phrase": "move forward"})
        assert locked.is_error

    asyncio.run(run())


def test_unknown_or_miscased_tool_names_are_handled():
    async def run():
        tools = RobotTools(AIConfig())
        assert (await tools.execute("End_Conversation", {})).content.startswith("Conversation closed")
        assert tools.conversation_ended
        assert (await tools.execute("fly", {})).is_error

    asyncio.run(run())
