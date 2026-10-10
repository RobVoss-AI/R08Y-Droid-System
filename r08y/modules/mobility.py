"""Safe mecanum mobility control for R08Y."""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Sequence, Tuple

from r08y.config import MobilityConfig
from r08y.hardware.freenove import FreenoveLibrary, discover_freenove_path

logger = logging.getLogger(__name__)


class MobilityError(RuntimeError):
    """Base mobility error."""


class MobilitySafetyError(MobilityError):
    """A requested movement failed a safety check."""


def mecanum_mix(forward: float, strafe: float, turn_left: float) -> Tuple[float, float, float, float]:
    """Return normalized (front-left, rear-left, front-right, rear-right).

    Positive forward moves forward. Positive strafe moves right. Positive turn
    rotates left. The ordering matches Freenove's ``set_motor_model`` API.
    """
    values = [
        forward + strafe - turn_left,
        forward - strafe - turn_left,
        forward - strafe + turn_left,
        forward + strafe + turn_left,
    ]
    peak = max(1.0, *(abs(value) for value in values))
    return tuple(value / peak for value in values)  # type: ignore[return-value]


class MobilityController(ABC):
    available: bool = False

    @abstractmethod
    async def start(self) -> None:
        ...

    @abstractmethod
    async def drive(self, forward: float, strafe: float, turn_left: float) -> None:
        ...

    async def move_for(
        self,
        forward: float,
        strafe: float,
        turn_left: float,
        duration: float,
    ) -> None:
        await self.drive(forward, strafe, turn_left)
        try:
            await asyncio.sleep(duration)
        finally:
            await self.emergency_stop()

    @abstractmethod
    async def emergency_stop(self) -> None:
        ...

    @abstractmethod
    async def stop(self) -> None:
        ...


class DisabledMobilityController(MobilityController):
    """No-op controller used when mobility is disabled or unavailable."""

    async def start(self) -> None:
        self.available = False

    async def drive(self, forward: float, strafe: float, turn_left: float) -> None:
        raise MobilityError("Mobility hardware is not available")

    async def emergency_stop(self) -> None:
        return

    async def stop(self) -> None:
        self.available = False


class FreenoveMobilityController(MobilityController):
    """Adapter for the official Freenove ``Ordinary_Car`` motor API."""

    def __init__(self, config: MobilityConfig, distance_sensor=None):
        self._config = config
        self._distance_sensor = distance_sensor
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mobility")
        self._motor = None
        self._watchdog_task: Optional[asyncio.Task] = None
        self._last_command = 0.0
        self._moving = False
        self._closed = False
        self.available = False

    def _init_hardware(self) -> None:
        library = FreenoveLibrary(self._config.freenove_library_path)
        motor_class = library.load_class("motor", "Ordinary_Car")
        self._motor = motor_class()
        self._motor.set_motor_model(0, 0, 0, 0)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._init_hardware)
        except Exception:
            logger.exception("Failed to initialize Freenove mobility")
            return
        self.available = True
        self._last_command = time.monotonic()
        self._watchdog_task = asyncio.create_task(self._watchdog_loop())
        logger.info("Freenove mecanum mobility ready; max duty=%d", self._config.max_duty)

    async def _check_forward_clearance(self, forward: float) -> None:
        if forward <= 0:
            return
        sensor = self._distance_sensor
        if sensor is None or not getattr(sensor, "available", False):
            if self._config.require_distance_for_forward:
                raise MobilitySafetyError("Forward motion blocked: distance sensor is unavailable")
            return
        distance = await sensor.read_cm()
        if distance is None:
            if self._config.require_distance_for_forward:
                raise MobilitySafetyError("Forward motion blocked: no valid distance reading")
            return
        if distance < self._config.obstacle_stop_cm:
            raise MobilitySafetyError(
                f"Forward motion blocked: obstacle at {distance:.1f} cm"
            )

    def _write_wheels(self, duties: Sequence[int]) -> None:
        if self._motor is None:
            raise MobilityError("Freenove motor driver is not initialized")
        self._motor.set_motor_model(*duties)

    async def drive(self, forward: float, strafe: float, turn_left: float) -> None:
        if not self.available:
            raise MobilityError("Mobility hardware is not available")
        forward = max(-1.0, min(1.0, forward))
        strafe = max(-1.0, min(1.0, strafe))
        turn_left = max(-1.0, min(1.0, turn_left))
        await self._check_forward_clearance(forward)

        mixed = mecanum_mix(forward, strafe, turn_left)
        duties = [
            int(value * self._config.max_duty * scale)
            for value, scale in zip(mixed, self._config.wheel_scale)
        ]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._write_wheels, duties)
        self._moving = any(duty != 0 for duty in duties)
        self._last_command = time.monotonic()
        logger.debug("Mobility command: %s", duties)

    async def move_for(
        self,
        forward: float,
        strafe: float,
        turn_left: float,
        duration: float,
    ) -> None:
        """Move briefly while continuing to guard forward clearance."""
        await self.drive(forward, strafe, turn_left)
        deadline = time.monotonic() + duration
        try:
            while time.monotonic() < deadline:
                await asyncio.sleep(min(0.1, max(0.0, deadline - time.monotonic())))
                if forward > 0:
                    await self._check_forward_clearance(forward)
        finally:
            await self.emergency_stop()

    async def emergency_stop(self) -> None:
        if self._motor is None:
            self._moving = False
            return
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._write_wheels, [0, 0, 0, 0])
        except Exception:
            logger.exception("Emergency stop failed")
        finally:
            self._moving = False
            self._last_command = time.monotonic()

    async def _watchdog_loop(self) -> None:
        try:
            while self.available:
                if self._moving and time.monotonic() - self._last_command > self._config.command_timeout:
                    logger.warning("Mobility watchdog stopped stale motor command")
                    await self.emergency_stop()
                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            pass

    def _close_hardware(self) -> None:
        if self._motor is not None:
            self._motor.close()
            self._motor = None

    async def stop(self) -> None:
        if self._closed:
            return
        await self.emergency_stop()
        self.available = False
        if self._watchdog_task:
            self._watchdog_task.cancel()
            try:
                await self._watchdog_task
            except asyncio.CancelledError:
                pass
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._close_hardware)
        finally:
            self._executor.shutdown(wait=False)
            self._closed = True


class MockMobilityController(MobilityController):
    """Deterministic fake used by the test suite and desktop development."""

    def __init__(self, config: MobilityConfig, distance_sensor=None):
        self._config = config
        self._distance_sensor = distance_sensor
        self.available = False
        self.history: List[Tuple[float, float, float, float]] = []

    async def start(self) -> None:
        self.available = True

    async def drive(self, forward: float, strafe: float, turn_left: float) -> None:
        if not self.available:
            raise MobilityError("Mock mobility is not started")
        if forward > 0 and self._config.require_distance_for_forward:
            if self._distance_sensor is None or not getattr(self._distance_sensor, "available", False):
                raise MobilitySafetyError("Forward motion blocked: distance sensor is unavailable")
            distance = await self._distance_sensor.read_cm()
            if distance is None or distance < self._config.obstacle_stop_cm:
                raise MobilitySafetyError("Forward motion blocked by distance guard")
        self.history.append((forward, strafe, turn_left, time.monotonic()))

    async def emergency_stop(self) -> None:
        self.history.append((0.0, 0.0, 0.0, time.monotonic()))

    async def stop(self) -> None:
        await self.emergency_stop()
        self.available = False


def create_mobility_controller(config: MobilityConfig, distance_sensor=None) -> MobilityController:
    if not config.enabled or config.driver == "disabled":
        logger.info("Mobility disabled in config")
        return DisabledMobilityController()
    if config.driver == "mock":
        return MockMobilityController(config, distance_sensor)
    if config.driver == "freenove":
        return FreenoveMobilityController(config, distance_sensor)
    if config.driver == "auto" and discover_freenove_path(config.freenove_library_path):
        return FreenoveMobilityController(config, distance_sensor)
    logger.warning("No supported mobility driver detected; wheels remain disabled")
    return DisabledMobilityController()
