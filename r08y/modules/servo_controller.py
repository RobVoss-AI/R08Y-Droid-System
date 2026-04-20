"""Servo controller module for R08Y.

Provides an abstract MotorController interface with implementations for
PCA9685 I2C, GPIO PWM, and a mock controller for development. A factory
function auto-detects the best available driver.
"""

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from r08y.config import ServoConfig

logger = logging.getLogger(__name__)


class MotorController(ABC):
    """Abstract base for motor/servo controllers.

    Used for both pan/tilt servos now and wheel motors in the future.
    """

    @abstractmethod
    async def set_angle(self, channel: int, angle: float) -> None:
        """Set servo to a specific angle."""
        ...

    @abstractmethod
    async def get_angle(self, channel: int) -> float:
        """Get current servo angle."""
        ...

    @abstractmethod
    async def home(self) -> None:
        """Return all servos to home position."""
        ...

    @abstractmethod
    async def start(self) -> None:
        """Initialize the controller hardware."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Release hardware resources."""
        ...


class PCA9685ServoController(MotorController):
    """Servo control via PCA9685 16-channel I2C PWM driver.

    Wiring: Pi SDA (GPIO2) → PCA9685 SDA, Pi SCL (GPIO3) → PCA9685 SCL,
    separate 5V power supply to PCA9685 V+ for servo power.
    """

    def __init__(self, config: ServoConfig):
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="servo")
        self._kit = None
        self._angles = {config.pan_channel: config.home_pan, config.tilt_channel: config.home_tilt}
        self.available = False

    def _init_hardware(self) -> bool:
        try:
            from adafruit_servokit import ServoKit
            self._kit = ServoKit(channels=16, address=self._config.i2c_address)
            # Set initial positions
            self._kit.servo[self._config.pan_channel].angle = self._config.home_pan
            self._kit.servo[self._config.tilt_channel].angle = self._config.home_tilt
            return True
        except Exception:
            logger.exception("Failed to initialize PCA9685")
            return False

    def _set_angle_sync(self, channel: int, angle: float) -> None:
        angle = max(self._config.pan_min if channel == self._config.pan_channel else self._config.tilt_min,
                     min(self._config.pan_max if channel == self._config.pan_channel else self._config.tilt_max,
                         angle))
        if self._kit:
            self._kit.servo[channel].angle = angle
        self._angles[channel] = angle

    async def set_angle(self, channel: int, angle: float) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._set_angle_sync, channel, angle)

    async def get_angle(self, channel: int) -> float:
        return self._angles.get(channel, 90.0)

    async def home(self) -> None:
        await self.set_angle(self._config.pan_channel, self._config.home_pan)
        await self.set_angle(self._config.tilt_channel, self._config.home_tilt)

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self.available = await loop.run_in_executor(self._executor, self._init_hardware)
        if self.available:
            logger.info("PCA9685 servo controller initialized at 0x%02X", self._config.i2c_address)
        else:
            logger.warning("PCA9685 not available")

    async def stop(self) -> None:
        await self.home()
        self._executor.shutdown(wait=False)


class GPIOServoController(MotorController):
    """Fallback servo control using RPi.GPIO software PWM.

    Less precise than PCA9685 but works without additional hardware.
    Default pins: GPIO 17 (pan), GPIO 18 (tilt).
    """

    # Map channel numbers to GPIO pins
    DEFAULT_PINS = {0: 17, 1: 18}

    def __init__(self, config: ServoConfig):
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gpio-servo")
        self._pwm = {}
        self._angles = {config.pan_channel: config.home_pan, config.tilt_channel: config.home_tilt}
        self.available = False

    def _init_hardware(self) -> bool:
        try:
            import RPi.GPIO as GPIO
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            for channel in [self._config.pan_channel, self._config.tilt_channel]:
                pin = self.DEFAULT_PINS.get(channel, 17 + channel)
                GPIO.setup(pin, GPIO.OUT)
                pwm = GPIO.PWM(pin, 50)  # 50Hz for servos
                pwm.start(self._angle_to_duty(90))
                self._pwm[channel] = pwm
            return True
        except Exception:
            logger.exception("Failed to initialize GPIO servos")
            return False

    @staticmethod
    def _angle_to_duty(angle: float) -> float:
        """Convert angle (0-180) to duty cycle (2.5-12.5%)."""
        return 2.5 + (angle / 180.0) * 10.0

    def _set_angle_sync(self, channel: int, angle: float) -> None:
        min_a = self._config.pan_min if channel == self._config.pan_channel else self._config.tilt_min
        max_a = self._config.pan_max if channel == self._config.pan_channel else self._config.tilt_max
        angle = max(min_a, min(max_a, angle))
        if channel in self._pwm:
            self._pwm[channel].ChangeDutyCycle(self._angle_to_duty(angle))
        self._angles[channel] = angle

    async def set_angle(self, channel: int, angle: float) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(self._executor, self._set_angle_sync, channel, angle)

    async def get_angle(self, channel: int) -> float:
        return self._angles.get(channel, 90.0)

    async def home(self) -> None:
        await self.set_angle(self._config.pan_channel, self._config.home_pan)
        await self.set_angle(self._config.tilt_channel, self._config.home_tilt)

    async def start(self) -> None:
        loop = asyncio.get_event_loop()
        self.available = await loop.run_in_executor(self._executor, self._init_hardware)
        if self.available:
            logger.info("GPIO servo controller initialized")

    async def stop(self) -> None:
        await self.home()
        for pwm in self._pwm.values():
            pwm.stop()
        try:
            import RPi.GPIO as GPIO
            GPIO.cleanup()
        except Exception:
            pass
        self._executor.shutdown(wait=False)


class MockServoController(MotorController):
    """Mock servo controller for development on non-Pi hardware.

    Logs movements and tracks virtual positions.
    """

    def __init__(self, config: ServoConfig):
        self._config = config
        self._angles = {config.pan_channel: config.home_pan, config.tilt_channel: config.home_tilt}
        self.available = True

    async def set_angle(self, channel: int, angle: float) -> None:
        min_a = self._config.pan_min if channel == self._config.pan_channel else self._config.tilt_min
        max_a = self._config.pan_max if channel == self._config.pan_channel else self._config.tilt_max
        angle = max(min_a, min(max_a, angle))
        old = self._angles.get(channel, 90)
        self._angles[channel] = angle
        if abs(old - angle) > 1:
            logger.debug("Mock servo ch%d: %.1f -> %.1f", channel, old, angle)

    async def get_angle(self, channel: int) -> float:
        return self._angles.get(channel, 90.0)

    async def home(self) -> None:
        await self.set_angle(self._config.pan_channel, self._config.home_pan)
        await self.set_angle(self._config.tilt_channel, self._config.home_tilt)

    async def start(self) -> None:
        logger.info("Mock servo controller active (no physical servos)")

    async def stop(self) -> None:
        pass


def create_servo_controller(config: ServoConfig) -> MotorController:
    """Factory: create the best available servo controller.

    auto: tries PCA9685 → GPIO → Mock
    """
    if not config.enabled:
        logger.info("Servos disabled in config")
        return MockServoController(config)

    if config.driver == "pca9685":
        return PCA9685ServoController(config)
    elif config.driver == "gpio":
        return GPIOServoController(config)
    elif config.driver == "mock":
        return MockServoController(config)

    # Auto-detect
    try:
        import adafruit_servokit  # noqa: F401
        import board  # noqa: F401
        logger.info("PCA9685 libraries detected — using I2C servo driver")
        return PCA9685ServoController(config)
    except ImportError:
        pass

    try:
        import RPi.GPIO  # noqa: F401
        logger.info("RPi.GPIO detected — using GPIO PWM servo driver")
        return GPIOServoController(config)
    except ImportError:
        pass

    logger.info("No servo hardware detected — using mock controller")
    return MockServoController(config)
