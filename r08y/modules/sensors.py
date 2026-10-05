"""Distance and light-sensor adapters for the Freenove chassis."""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional

from r08y.config import DistanceConfig, LightSensorConfig
from r08y.hardware.freenove import FreenoveLibrary, discover_freenove_path

logger = logging.getLogger(__name__)


class DistanceSensor(ABC):
    available: bool = False

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def read_cm(self) -> Optional[float]:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...


class DisabledDistanceSensor(DistanceSensor):
    async def start(self) -> None:
        self.available = False

    async def read_cm(self) -> Optional[float]:
        return None

    async def stop(self) -> None:
        self.available = False


class FreenoveDistanceSensor(DistanceSensor):
    def __init__(self, config: DistanceConfig):
        self._config = config
        self._sensor = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="distance")
        self.available = False

    def _init(self) -> None:
        library = FreenoveLibrary(self._config.freenove_library_path)
        sensor_class = library.load_class("ultrasonic", "Ultrasonic")
        self._sensor = sensor_class(max_distance=self._config.max_distance_m)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._init)
            self.available = True
            logger.info("Freenove ultrasonic sensor ready")
        except Exception:
            logger.exception("Failed to initialize Freenove ultrasonic sensor")

    async def read_cm(self) -> Optional[float]:
        if not self.available or self._sensor is None:
            return None
        loop = asyncio.get_running_loop()
        try:
            value = await asyncio.wait_for(
                loop.run_in_executor(self._executor, self._sensor.get_distance),
                timeout=self._config.timeout_seconds,
            )
            return float(value) if value is not None else None
        except (asyncio.TimeoutError, TypeError, ValueError):
            return None

    def _close(self) -> None:
        if self._sensor is not None:
            self._sensor.close()
            self._sensor = None

    async def stop(self) -> None:
        self.available = False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._close)
        finally:
            self._executor.shutdown(wait=False)


class GPIODistanceSensor(DistanceSensor):
    """Generic gpiozero adapter, available only with explicit pins."""

    def __init__(self, config: DistanceConfig):
        self._config = config
        self._sensor = None
        self.available = False

    async def start(self) -> None:
        if self._config.trigger_pin is None or self._config.echo_pin is None:
            logger.error("GPIO distance driver requires explicit trigger and echo pins")
            return
        try:
            from gpiozero import DistanceSensor as GPIOZeroDistanceSensor

            self._sensor = GPIOZeroDistanceSensor(
                echo=self._config.echo_pin,
                trigger=self._config.trigger_pin,
                max_distance=self._config.max_distance_m,
            )
            self.available = True
        except Exception:
            logger.exception("Failed to initialize GPIO ultrasonic sensor")

    async def read_cm(self) -> Optional[float]:
        if not self.available or self._sensor is None:
            return None
        try:
            value = await asyncio.to_thread(lambda: self._sensor.distance)
            return round(float(value) * 100, 1)
        except Exception:
            return None

    async def stop(self) -> None:
        if self._sensor is not None:
            await asyncio.to_thread(self._sensor.close)
        self.available = False


class MockDistanceSensor(DistanceSensor):
    def __init__(self, distance_cm: Optional[float] = 100.0):
        self.distance_cm = distance_cm
        self.available = False

    async def start(self) -> None:
        self.available = True

    async def read_cm(self) -> Optional[float]:
        return self.distance_cm

    async def stop(self) -> None:
        self.available = False


def create_distance_sensor(config: DistanceConfig) -> DistanceSensor:
    if not config.enabled or config.driver == "disabled":
        return DisabledDistanceSensor()
    if config.driver == "mock":
        return MockDistanceSensor()
    if config.driver == "freenove":
        return FreenoveDistanceSensor(config)
    if config.driver == "gpio":
        return GPIODistanceSensor(config)
    if config.driver == "auto" and discover_freenove_path(config.freenove_library_path):
        return FreenoveDistanceSensor(config)
    if config.driver == "auto" and config.trigger_pin is not None and config.echo_pin is not None:
        return GPIODistanceSensor(config)
    logger.warning("No distance-sensor driver detected")
    return DisabledDistanceSensor()


@dataclass(frozen=True)
class LightReading:
    left_volts: float
    right_volts: float


class LightSensors(ABC):
    available: bool = False

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def read(self) -> Optional[LightReading]:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...


class DisabledLightSensors(LightSensors):
    async def start(self) -> None:
        self.available = False

    async def read(self) -> Optional[LightReading]:
        return None

    async def stop(self) -> None:
        self.available = False


class FreenoveLightSensors(LightSensors):
    def __init__(self, config: LightSensorConfig):
        self._config = config
        self._adc = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="light-sensors")
        self.available = False

    def _init(self) -> None:
        library = FreenoveLibrary(self._config.freenove_library_path)
        adc_class = library.load_class("adc", "ADC")
        with library.parameter_working_directory():
            self._adc = adc_class()

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._init)
            self.available = True
            logger.info("Freenove light sensors ready")
        except Exception:
            logger.exception("Failed to initialize Freenove light sensors")

    def _read_sync(self) -> LightReading:
        return LightReading(
            left_volts=float(self._adc.read_adc(self._config.left_channel)),
            right_volts=float(self._adc.read_adc(self._config.right_channel)),
        )

    async def read(self) -> Optional[LightReading]:
        if not self.available or self._adc is None:
            return None
        try:
            return await asyncio.get_running_loop().run_in_executor(self._executor, self._read_sync)
        except Exception:
            logger.exception("Light sensor read failed")
            return None

    def _close(self) -> None:
        if self._adc is not None:
            self._adc.close_i2c()
            self._adc = None

    async def stop(self) -> None:
        self.available = False
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._close)
        finally:
            self._executor.shutdown(wait=False)


class MockLightSensors(LightSensors):
    def __init__(self, left: float = 1.2, right: float = 1.2):
        self.reading = LightReading(left, right)
        self.available = False

    async def start(self) -> None:
        self.available = True

    async def read(self) -> Optional[LightReading]:
        return self.reading

    async def stop(self) -> None:
        self.available = False


def create_light_sensors(config: LightSensorConfig) -> LightSensors:
    if not config.enabled or config.driver == "disabled":
        return DisabledLightSensors()
    if config.driver == "mock":
        return MockLightSensors()
    if config.driver in {"auto", "freenove"} and discover_freenove_path(config.freenove_library_path):
        return FreenoveLightSensors(config)
    logger.warning("No light-sensor driver detected")
    return DisabledLightSensors()
