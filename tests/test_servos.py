"""Tests for the servo controller module."""

import asyncio

from r08y.config import ServoConfig
from r08y.modules.servo_controller import MockServoController


async def _test_mock_servo():
    config = ServoConfig(pan_min=30, pan_max=150, tilt_min=30, tilt_max=150, home_pan=90, home_tilt=90)
    servo = MockServoController(config)
    await servo.start()
    assert servo.available

    # Test home position
    assert await servo.get_angle(0) == 90
    assert await servo.get_angle(1) == 90

    # Test set angle
    await servo.set_angle(0, 45)
    assert await servo.get_angle(0) == 45

    # Test clamping at limits
    await servo.set_angle(0, 200)  # Over max
    assert await servo.get_angle(0) == 150

    await servo.set_angle(0, 10)  # Under min
    assert await servo.get_angle(0) == 30

    # Test home
    await servo.home()
    assert await servo.get_angle(0) == 90
    assert await servo.get_angle(1) == 90

    await servo.stop()
    assert not servo.available


def test_mock_servo():
    asyncio.run(_test_mock_servo())


async def _test_factory_returns_mock():
    from r08y.modules.servo_controller import create_servo_controller
    config = ServoConfig(driver="mock")
    controller = create_servo_controller(config)
    assert isinstance(controller, MockServoController)
    await controller.start()
    assert controller.available


def test_factory_returns_mock():
    asyncio.run(_test_factory_returns_mock())


if __name__ == "__main__":
    test_mock_servo()
    test_factory_returns_mock()
    print("All servo tests passed!")
