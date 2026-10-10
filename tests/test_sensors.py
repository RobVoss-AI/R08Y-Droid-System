import asyncio

from r08y.modules.sensors import MockDistanceSensor, MockLightSensors


def test_mock_sensors_lifecycle():
    async def run():
        distance = MockDistanceSensor(42.5)
        light = MockLightSensors(1.1, 2.2)
        await asyncio.gather(distance.start(), light.start())
        assert await distance.read_cm() == 42.5
        reading = await light.read()
        assert reading.left_volts == 1.1
        assert reading.right_volts == 2.2
        await asyncio.gather(distance.stop(), light.stop())
        assert not distance.available
        assert not light.available

    asyncio.run(run())
