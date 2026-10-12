"""Face tracker module for R08Y.

Uses a PID controller to smoothly track a detected face by adjusting
pan/tilt servos. Includes a dead zone to prevent jitter and an optional
patrol scan mode when no face is visible.
"""

import asyncio
import logging
import time
from typing import Optional

from r08y.config import ServoConfig, TrackerConfig
from r08y.modules.face_detector import FaceDetection
from r08y.modules.expression_controller import ExpressionController

logger = logging.getLogger(__name__)


class PIDController:
    """Simple PID controller with anti-windup clamping."""

    def __init__(self, kp: float, ki: float, kd: float):
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self._integral = 0.0
        self._prev_error = 0.0
        self._prev_time: Optional[float] = None

    def compute(self, error: float) -> float:
        now = time.monotonic()
        dt = now - self._prev_time if self._prev_time else 0.033
        self._prev_time = now

        # Proportional
        p = self.kp * error

        # Integral with anti-windup
        self._integral += error * dt
        self._integral = max(-50.0, min(50.0, self._integral))
        i = self.ki * self._integral

        # Derivative
        d = self.kd * (error - self._prev_error) / dt if dt > 0 else 0.0
        self._prev_error = error

        return p + i + d

    def reset(self) -> None:
        self._integral = 0.0
        self._prev_error = 0.0
        self._prev_time = None


class FaceTracker:
    """Tracks a face by adjusting pan/tilt servos using PID control."""

    def __init__(
        self,
        expression: ExpressionController,
        servo_config: ServoConfig,
        tracker_config: TrackerConfig,
        frame_width: int = 640,
        frame_height: int = 480,
    ):
        self._expression = expression
        self._servo_config = servo_config
        self._tracker_config = tracker_config
        self._frame_width = frame_width
        self._frame_height = frame_height

        self._pan_pid = PIDController(
            tracker_config.pid_kp, tracker_config.pid_ki, tracker_config.pid_kd
        )
        self._tilt_pid = PIDController(
            tracker_config.pid_kp, tracker_config.pid_ki, tracker_config.pid_kd
        )

        self._last_face_time = time.monotonic()
        self._patrol_direction: float = 1.0  # 1.0 = right, -1.0 = left
        self._patrol_active = False
        self._idle_home_set = False
        self._prev_update_time: Optional[float] = None

    async def update(self, detection: Optional[FaceDetection]) -> None:
        """Update servo positions based on face detection (or lack thereof).

        Call this every frame from the vision pipeline.
        """
        now = time.monotonic()

        if detection is not None:
            self._last_face_time = now
            self._patrol_active = False
            self._idle_home_set = False
            await self._track_face(detection)
        elif now - self._last_face_time > self._tracker_config.no_face_timeout:
            if self._tracker_config.patrol_enabled:
                if not self._patrol_active:
                    self._patrol_active = True
                    self._pan_pid.reset()
                    self._tilt_pid.reset()
                    logger.debug(
                        "No face for %.1fs; starting patrol",
                        self._tracker_config.no_face_timeout,
                    )
                await self._patrol(now)
            elif not self._idle_home_set:
                self._patrol_active = False
                self._pan_pid.reset()
                self._tilt_pid.reset()
                await self._expression.set_gaze_target(
                    self._servo_config.home_pan,
                    self._servo_config.home_tilt,
                )
                self._idle_home_set = True

        self._prev_update_time = now

    async def _track_face(self, detection: FaceDetection) -> None:
        """Adjust servos to center the face in frame."""
        face_cx, face_cy = detection.center
        frame_cx = self._frame_width / 2
        frame_cy = self._frame_height / 2

        # Normalized error: -1.0 (left/top) to +1.0 (right/bottom)
        error_x = (frame_cx - face_cx) / frame_cx
        error_y = (frame_cy - face_cy) / frame_cy

        # Dead zone — ignore tiny errors to prevent servo jitter
        dz = self._tracker_config.dead_zone
        if abs(error_x) < dz:
            error_x = 0.0
        if abs(error_y) < dz:
            error_y = 0.0

        if error_x == 0.0 and error_y == 0.0:
            return

        # PID outputs are angle deltas
        pan_delta = self._pan_pid.compute(error_x)
        tilt_delta = self._tilt_pid.compute(error_y)

        # Apply deltas to current positions
        current_pan, current_tilt = await self._expression.get_gaze_target()

        new_pan = current_pan + pan_delta
        new_tilt = current_tilt + tilt_delta

        await self._expression.set_gaze_target(new_pan, new_tilt)

    async def _patrol(self, now: float) -> None:
        """Slowly sweep the camera left-right when no face is detected."""
        dt = now - self._prev_update_time if self._prev_update_time else 0.033
        speed = self._tracker_config.patrol_speed

        current_pan, current_tilt = await self._expression.get_gaze_target()
        new_pan = current_pan + self._patrol_direction * speed * dt

        # Reverse direction at limits
        if new_pan >= self._servo_config.pan_max:
            new_pan = self._servo_config.pan_max
            self._patrol_direction = -1.0
        elif new_pan <= self._servo_config.pan_min:
            new_pan = self._servo_config.pan_min
            self._patrol_direction = 1.0

        # Slowly return tilt to home during patrol
        home_tilt = self._servo_config.home_tilt
        if abs(current_tilt - home_tilt) > 1.0:
            tilt_step = 10.0 * dt * (1.0 if home_tilt > current_tilt else -1.0)
            current_tilt += tilt_step
        await self._expression.set_gaze_target(new_pan, current_tilt)

    async def go_home(self) -> None:
        """Return servos to home position."""
        self._pan_pid.reset()
        self._tilt_pid.reset()
        await self._expression.home()
