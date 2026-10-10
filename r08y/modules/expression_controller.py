"""Single-owner pan/tilt expression and gaze control for R08Y."""

import asyncio
import logging
import math
import time
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Dict, Optional, Tuple

from r08y.config import ExpressionConfig, ServoConfig
from r08y.modules.servo_controller import MotorController

logger = logging.getLogger(__name__)


class InteractionState(str, Enum):
    """High-level states that shape R08Y's ambient body language."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"


@dataclass(frozen=True)
class GestureStep:
    pan_offset: float
    tilt_offset: float
    duration: float


@dataclass(frozen=True)
class GestureDefinition:
    steps: Tuple[GestureStep, ...]
    return_to_gaze: bool = True
    anchor_home: bool = False


@dataclass
class _MotionRequest:
    name: str
    steps: Tuple[Tuple[float, float, float], ...]
    return_to_gaze: bool
    persist_final: bool
    future: asyncio.Future


def _step(pan: float, tilt: float, duration: float) -> GestureStep:
    return GestureStep(pan, tilt, duration)


# Gestures describe intent in bounded offsets. Only ExpressionController turns
# them into physical servo commands.
GESTURES: Dict[str, GestureDefinition] = {
    "acknowledge": GestureDefinition((_step(0, -5, 0.12), _step(0, 2, 0.12))),
    "nod": GestureDefinition(
        (_step(0, -10, 0.16), _step(0, 9, 0.18), _step(0, -7, 0.16))
    ),
    "nod_emphasis": GestureDefinition(
        (_step(0, -12, 0.18), _step(0, 10, 0.2), _step(0, -8, 0.18))
    ),
    "shake": GestureDefinition(
        (_step(-14, 0, 0.17), _step(14, 0, 0.2), _step(-10, 0, 0.17))
    ),
    "greet": GestureDefinition(
        (_step(-8, -5, 0.18), _step(8, 3, 0.2), _step(0, -3, 0.16))
    ),
    "curious": GestureDefinition((_step(8, -8, 0.28), _step(11, -5, 0.22))),
    "pleased": GestureDefinition(
        (_step(-4, -6, 0.2), _step(4, -3, 0.18), _step(0, -5, 0.16))
    ),
    "uncertain": GestureDefinition(
        (_step(-8, 6, 0.25), _step(7, 8, 0.25), _step(0, 5, 0.2))
    ),
    "dance": GestureDefinition(
        (
            _step(-20, -7, 0.2),
            _step(20, 7, 0.22),
            _step(-16, 7, 0.2),
            _step(16, -7, 0.22),
        )
    ),
    "alert": GestureDefinition(
        (_step(-28, 0, 0.22), _step(28, 0, 0.28), _step(-20, 0, 0.22))
    ),
    "look_left": GestureDefinition((_step(-30, 0, 0.35),), False, True),
    "look_right": GestureDefinition((_step(30, 0, 0.35),), False, True),
    "look_up": GestureDefinition((_step(0, -20, 0.3),), False, True),
    "look_down": GestureDefinition((_step(0, 18, 0.3),), False, True),
    "scan_left": GestureDefinition((_step(-28, 0, 0.35),), False, True),
    "scan_right": GestureDefinition((_step(28, 0, 0.35),), False, True),
    "look_audience": GestureDefinition(
        (_step(-22, 3, 0.35), _step(22, 3, 0.45), _step(0, 0, 0.3)),
        anchor_home=True,
    ),
    "dramatic_pause": GestureDefinition((_step(0, -7, 0.3),), False, True),
    "sweep": GestureDefinition(
        (_step(-30, 0, 0.4), _step(30, 0, 0.55), _step(0, 0, 0.35)),
        anchor_home=True,
    ),
    "lean_in": GestureDefinition((_step(0, 8, 0.3),), False, True),
    "scan": GestureDefinition(
        (_step(-22, 0, 0.35), _step(22, 0, 0.45), _step(0, 0, 0.3)),
        anchor_home=True,
    ),
}


class ExpressionController:
    """Own all runtime pan/tilt writes and blend gaze with expression."""

    def __init__(
        self,
        servo: MotorController,
        servo_config: ServoConfig,
        config: ExpressionConfig,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._servo = servo
        self._servo_config = servo_config
        self._config = config
        self._sleep = sleep
        self._clock = clock
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(
            maxsize=config.queue_size
        )
        self._worker_task: Optional[asyncio.Task] = None
        self._active_cancel: Optional[asyncio.Event] = None
        self._sequence = 0
        self._running = False
        self._state = InteractionState.IDLE
        self._gaze_pan = float(servo_config.home_pan)
        self._gaze_tilt = float(servo_config.home_tilt)
        self._pose_pan = self._gaze_pan
        self._pose_tilt = self._gaze_tilt

    @property
    def available(self) -> bool:
        return bool(
            self._config.enabled
            and self._running
            and getattr(self._servo, "available", False)
        )

    @property
    def interaction_state(self) -> InteractionState:
        return self._state

    async def start(self) -> None:
        if self._running:
            return
        if not self._config.enabled:
            logger.info("Expression controller disabled")
            return
        if not getattr(self._servo, "available", False):
            logger.warning("Expression controller unavailable because pan/tilt is offline")
            return
        self._pose_pan = await self._servo.get_angle(self._servo_config.pan_channel)
        self._pose_tilt = await self._servo.get_angle(self._servo_config.tilt_channel)
        self._gaze_pan = self._pose_pan
        self._gaze_tilt = self._pose_tilt
        self._running = True
        self._worker_task = asyncio.create_task(
            self._worker(), name="r08y-expression-controller"
        )
        logger.info("Expression controller started")

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        await self.cancel_motion()
        if self._worker_task is not None:
            self._worker_task.cancel()
            await asyncio.gather(self._worker_task, return_exceptions=True)
            self._worker_task = None
        if getattr(self._servo, "available", False):
            await self._move_to(
                float(self._servo_config.home_pan),
                float(self._servo_config.home_tilt),
                0.25,
                None,
            )
        logger.info("Expression controller stopped at home position")

    async def set_interaction_state(self, state: InteractionState | str) -> None:
        self._state = InteractionState(state)

    async def set_gaze_target(self, pan: float, tilt: float) -> None:
        """Update the tracked gaze target without writing to hardware directly."""
        self._gaze_pan, self._gaze_tilt = self._clamp(pan, tilt)

    async def get_gaze_target(self) -> Tuple[float, float]:
        return self._gaze_pan, self._gaze_tilt

    async def get_pose(self) -> Tuple[float, float]:
        return self._pose_pan, self._pose_tilt

    async def look_at(
        self,
        pan: float,
        tilt: float,
        *,
        duration: float = 0.3,
        wait: bool = True,
        priority: int = 20,
    ) -> bool:
        pan, tilt = self._clamp(pan, tilt)
        self._gaze_pan, self._gaze_tilt = pan, tilt
        return await self._enqueue(
            name="look_at",
            steps=((pan, tilt, max(0.0, duration)),),
            return_to_gaze=False,
            persist_final=True,
            priority=priority,
            wait=wait,
        )

    async def home(self, *, wait: bool = True) -> bool:
        await self.cancel_motion()
        return await self.look_at(
            self._servo_config.home_pan,
            self._servo_config.home_tilt,
            duration=0.3,
            wait=wait,
            priority=0,
        )

    async def perform_gesture(
        self,
        name: str,
        *,
        wait: bool = True,
        priority: int = 10,
    ) -> bool:
        if name == "center":
            return await self.home(wait=wait)
        definition = GESTURES.get(name)
        if definition is None:
            raise ValueError(f"Unknown expression gesture: {name}")
        anchor_pan = (
            float(self._servo_config.home_pan)
            if definition.anchor_home
            else self._gaze_pan
        )
        anchor_tilt = (
            float(self._servo_config.home_tilt)
            if definition.anchor_home
            else self._gaze_tilt
        )
        scale = self._config.gesture_scale
        steps = tuple(
            (*self._clamp(
                anchor_pan + step.pan_offset * scale,
                anchor_tilt + step.tilt_offset * scale,
            ), step.duration)
            for step in definition.steps
        )
        return await self._enqueue(
            name=name,
            steps=steps,
            return_to_gaze=definition.return_to_gaze,
            persist_final=not definition.return_to_gaze,
            priority=priority,
            wait=wait,
        )

    async def cancel_motion(self) -> None:
        """Cancel the active gesture and resolve queued requests as cancelled."""
        if self._active_cancel is not None:
            self._active_cancel.set()
        while True:
            try:
                _, _, request = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if not request.future.done():
                request.future.set_result(False)
            self._queue.task_done()
        await asyncio.sleep(0)

    async def _enqueue(
        self,
        *,
        name: str,
        steps: Tuple[Tuple[float, float, float], ...],
        return_to_gaze: bool,
        persist_final: bool,
        priority: int,
        wait: bool,
    ) -> bool:
        if not self.available:
            return False
        future = asyncio.get_running_loop().create_future()
        request = _MotionRequest(
            name=name,
            steps=steps,
            return_to_gaze=return_to_gaze,
            persist_final=persist_final,
            future=future,
        )
        self._sequence += 1
        try:
            self._queue.put_nowait((priority, self._sequence, request))
        except asyncio.QueueFull:
            logger.warning("Expression queue full; rejected gesture '%s'", name)
            return False
        if not wait:
            return True
        return bool(await future)

    async def _worker(self) -> None:
        while self._running:
            try:
                _, _, request = await asyncio.wait_for(
                    self._queue.get(), timeout=self._config.update_interval
                )
            except asyncio.TimeoutError:
                await self._ambient_tick()
                continue

            cancel = asyncio.Event()
            self._active_cancel = cancel
            completed = False
            try:
                completed = await self._execute(request, cancel)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Expression gesture '%s' failed", request.name)
            finally:
                self._active_cancel = None
                if not request.future.done():
                    request.future.set_result(completed)
                self._queue.task_done()

    async def _execute(
        self, request: _MotionRequest, cancel: asyncio.Event
    ) -> bool:
        for pan, tilt, duration in request.steps:
            if not await self._move_to(pan, tilt, duration, cancel):
                return False
        if request.persist_final and request.steps:
            self._gaze_pan, self._gaze_tilt = request.steps[-1][:2]
        if request.return_to_gaze:
            return await self._move_to(
                self._gaze_pan, self._gaze_tilt, 0.22, cancel
            )
        return True

    async def _ambient_tick(self) -> None:
        target_pan, target_tilt = self._ambient_target()
        max_step = self._config.max_step_degrees
        next_pan = self._approach(self._pose_pan, target_pan, max_step)
        next_tilt = self._approach(self._pose_tilt, target_tilt, max_step)
        await self._write_pose(next_pan, next_tilt)

    def _ambient_target(self) -> Tuple[float, float]:
        if not self._config.ambient_enabled:
            return self._gaze_pan, self._gaze_tilt
        now = self._clock()
        if self._state == InteractionState.IDLE:
            phase = now * (2 * math.pi / 6.0)
            pan = self._gaze_pan + math.sin(phase) * self._config.idle_sway_degrees
            tilt = self._gaze_tilt + math.sin(phase * 0.5) * 0.4
        elif self._state == InteractionState.LISTENING:
            pan = self._gaze_pan
            tilt = self._gaze_tilt - 1.5
        elif self._state == InteractionState.THINKING:
            phase = now * (2 * math.pi / 3.5)
            pan = self._gaze_pan + math.sin(phase) * self._config.thinking_sway_degrees
            tilt = self._gaze_tilt + 1.0
        else:
            phase = now * (2 * math.pi / 1.2)
            pan = self._gaze_pan + math.sin(phase * 0.5) * 0.6
            tilt = self._gaze_tilt + math.sin(phase) * self._config.speaking_bob_degrees
        return self._clamp(pan, tilt)

    async def _move_to(
        self,
        pan: float,
        tilt: float,
        duration: float,
        cancel: Optional[asyncio.Event],
    ) -> bool:
        pan, tilt = self._clamp(pan, tilt)
        interval = self._config.update_interval
        start_pan, start_tilt = self._pose_pan, self._pose_tilt
        timed_steps = math.ceil(duration / interval) if duration else 1
        distance_steps = math.ceil(
            1.5 * max(abs(pan - start_pan), abs(tilt - start_tilt))
            / self._config.max_step_degrees
        )
        steps = max(1, timed_steps, distance_steps)
        for index in range(1, steps + 1):
            if cancel is not None and cancel.is_set():
                return False
            progress = index / steps
            eased = progress * progress * (3.0 - 2.0 * progress)
            next_pan = start_pan + (pan - start_pan) * eased
            next_tilt = start_tilt + (tilt - start_tilt) * eased
            await self._write_pose(next_pan, next_tilt)
            if index < steps:
                await self._sleep(interval)
        return True

    async def _write_pose(self, pan: float, tilt: float) -> None:
        pan, tilt = self._clamp(pan, tilt)
        if abs(pan - self._pose_pan) >= 0.05:
            await self._servo.set_angle(self._servo_config.pan_channel, pan)
            self._pose_pan = pan
        if abs(tilt - self._pose_tilt) >= 0.05:
            await self._servo.set_angle(self._servo_config.tilt_channel, tilt)
            self._pose_tilt = tilt

    def _clamp(self, pan: float, tilt: float) -> Tuple[float, float]:
        return (
            max(self._servo_config.pan_min, min(self._servo_config.pan_max, pan)),
            max(self._servo_config.tilt_min, min(self._servo_config.tilt_max, tilt)),
        )

    @staticmethod
    def _approach(current: float, target: float, amount: float) -> float:
        if target > current:
            return min(target, current + amount)
        return max(target, current - amount)
