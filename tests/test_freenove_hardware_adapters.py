import asyncio
import json

import pytest

from r08y.config import DistanceConfig, LightSensorConfig, MobilityConfig, ServoConfig
from r08y.modules.mobility import FreenoveMobilityController, MobilitySafetyError
from r08y.modules.sensors import FreenoveDistanceSensor, FreenoveLightSensors, MockDistanceSensor
from r08y.modules.servo_controller import FreenoveServoController


def _fake_freenove_tree(path):
    (path / "motor.py").write_text(
        "class Ordinary_Car:\n"
        "    def __init__(self): self.calls = []; self.closed = False\n"
        "    def set_motor_model(self, *values): self.calls.append(tuple(values))\n"
        "    def close(self): self.closed = True\n"
    )
    (path / "servo.py").write_text(
        "class Servo:\n"
        "    def __init__(self): self.calls = []\n"
        "    def set_servo_pwm(self, channel, angle): self.calls.append((channel, angle))\n"
    )
    (path / "ultrasonic.py").write_text(
        "class Ultrasonic:\n"
        "    def __init__(self, max_distance): self.max_distance = max_distance; self.closed = False\n"
        "    def get_distance(self): return 37.5\n"
        "    def close(self): self.closed = True\n"
    )
    (path / "adc.py").write_text(
        "class ADC:\n"
        "    def __init__(self): self.closed = False\n"
        "    def read_adc(self, channel): return 1.25 + channel\n"
        "    def close_i2c(self): self.closed = True\n"
    )
    (path / "params.json").write_text(
        json.dumps({"Connect_Version": 2, "Pcb_Version": 2, "Pi_Version": 1})
    )


def test_official_freenove_mobility_api_is_guarded_and_stopped(tmp_path):
    _fake_freenove_tree(tmp_path)

    async def run():
        distance = MockDistanceSensor(100)
        await distance.start()
        controller = FreenoveMobilityController(
            MobilityConfig(
                enabled=True,
                driver="freenove",
                freenove_library_path=str(tmp_path),
                max_duty=2000,
                wheel_scale=[1.0, -1.0, 0.5, -0.5],
            ),
            distance,
        )
        await controller.start()
        assert controller.available
        motor = controller._motor
        assert motor.calls[0] == (0, 0, 0, 0)
        await controller.drive(0.5, 0, 0)
        assert motor.calls[-1] == (1000, -1000, 500, -500)
        await controller.stop()
        assert motor.calls[-1] == (0, 0, 0, 0)
        assert motor.closed

    asyncio.run(run())


def test_forward_motion_rechecks_clearance_while_moving(tmp_path):
    _fake_freenove_tree(tmp_path)

    class ClosingDistance:
        available = True

        def __init__(self):
            self.values = [100.0, 10.0]

        async def read_cm(self):
            return self.values.pop(0) if self.values else 10.0

    async def run():
        controller = FreenoveMobilityController(
            MobilityConfig(
                enabled=True,
                driver="freenove",
                freenove_library_path=str(tmp_path),
                obstacle_stop_cm=30,
            ),
            ClosingDistance(),
        )
        await controller.start()
        motor = controller._motor
        with pytest.raises(MobilitySafetyError):
            await controller.move_for(0.3, 0, 0, 0.3)
        assert motor.calls[-1] == (0, 0, 0, 0)
        await controller.stop()

    asyncio.run(run())


def test_official_freenove_servo_api_uses_logical_channels_and_limits(tmp_path):
    _fake_freenove_tree(tmp_path)

    async def run():
        controller = FreenoveServoController(
            ServoConfig(driver="freenove", freenove_library_path=str(tmp_path))
        )
        await controller.start()
        servo = controller._servo
        assert servo.calls[:2] == [("0", 90), ("1", 90)]
        await controller.set_angle(0, 999)
        assert servo.calls[-1] == ("0", 150)
        await controller.stop()
        assert not controller.available

    asyncio.run(run())


def test_official_freenove_sensor_apis_are_wrapped(tmp_path):
    _fake_freenove_tree(tmp_path)

    async def run():
        distance = FreenoveDistanceSensor(
            DistanceConfig(driver="freenove", freenove_library_path=str(tmp_path))
        )
        lights = FreenoveLightSensors(
            LightSensorConfig(driver="freenove", freenove_library_path=str(tmp_path))
        )
        await lights.start()
        await distance.start()
        assert await distance.read_cm() == 37.5
        reading = await lights.read()
        assert reading.left_volts == 1.25
        assert reading.right_volts == 2.25
        sensor = distance._sensor
        adc = lights._adc
        await distance.stop()
        await lights.stop()
        assert sensor.closed
        assert adc.closed

    asyncio.run(run())
