import asyncio

from r08y.config import MobilityConfig
from r08y.modules.mobility import (
    MobilitySafetyError,
    MockMobilityController,
    mecanum_mix,
)
from r08y.modules.sensors import MockDistanceSensor


def test_mecanum_mix_cardinal_directions():
    assert mecanum_mix(1, 0, 0) == (1, 1, 1, 1)
    assert mecanum_mix(-1, 0, 0) == (-1, -1, -1, -1)
    assert mecanum_mix(0, 1, 0) == (1, -1, -1, 1)
    assert mecanum_mix(0, 0, 1) == (-1, -1, 1, 1)


def test_mecanum_mix_normalizes_diagonal_commands():
    values = mecanum_mix(1, 1, 1)
    assert max(abs(value) for value in values) == 1


async def _exercise_guard(distance_value):
    sensor = MockDistanceSensor(distance_value)
    await sensor.start()
    controller = MockMobilityController(
        MobilityConfig(enabled=True, driver="mock", obstacle_stop_cm=30), sensor
    )
    await controller.start()
    return controller


def test_forward_motion_passes_with_clear_distance():
    async def run():
        controller = await _exercise_guard(100)
        await controller.move_for(0.4, 0, 0, 0)
        assert controller.history[0][:3] == (0.4, 0, 0)
        assert controller.history[-1][:3] == (0, 0, 0)

    asyncio.run(run())


def test_forward_motion_is_blocked_by_obstacle():
    async def run():
        controller = await _exercise_guard(10)
        try:
            await controller.drive(0.4, 0, 0)
        except MobilitySafetyError:
            pass
        else:
            raise AssertionError("Expected obstacle guard to block forward motion")

    asyncio.run(run())


def test_reverse_motion_does_not_require_distance():
    async def run():
        controller = await _exercise_guard(None)
        await controller.drive(-0.3, 0, 0)
        assert controller.history[-1][0] == -0.3

    asyncio.run(run())
