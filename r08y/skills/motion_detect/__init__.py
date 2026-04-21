"""Motion Detection skill — detect movement via frame differencing.

Hooks into the vision pipeline, compares consecutive frames, and emits
events when significant motion is detected. Useful for security alerts.

Events emitted: motion.detected, motion.cleared
"""

import asyncio
import time
from typing import Optional

import cv2
import numpy as np

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._prev_gray: Optional[np.ndarray] = None
        self._motion_active = False
        self._last_motion_time: float = 0
        self._monitoring = False
        self._threshold = 25
        self._min_area = 500

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="motion_detect",
            version="1.0.0",
            description="Detect motion via frame differencing for security alerts",
            requires={"camera"},
            pipeline_hooks=[PipelineHook.VISION_FRAME],
            vision_frame_interval=0.2,  # 5 FPS for motion detection
            commands=[
                VoiceCommand(triggers=["start monitoring", "watch mode", "security mode"], handler="cmd_start"),
                VoiceCommand(triggers=["stop monitoring", "stop watching"], handler="cmd_stop"),
                VoiceCommand(triggers=["any motion", "is anyone there", "detect motion"], handler="cmd_status"),
            ],
            emits_events=["motion.detected", "motion.cleared"],
        )

    async def on_vision_frame(self, frame: np.ndarray) -> None:
        if not self._monitoring:
            return

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (21, 21), 0)

        if self._prev_gray is None:
            self._prev_gray = gray
            return

        delta = cv2.absdiff(self._prev_gray, gray)
        thresh = cv2.threshold(delta, self._threshold, 255, cv2.THRESH_BINARY)[1]
        thresh = cv2.dilate(thresh, None, iterations=2)

        contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        motion_detected = any(cv2.contourArea(c) > self._min_area for c in contours)

        if motion_detected and not self._motion_active:
            self._motion_active = True
            self._last_motion_time = time.monotonic()
            await self.ctx.event_bus.emit("motion.detected", source=self.name)
            self.ctx.logger.info("Motion detected!")
            if self.ctx.tts.available:
                await self.ctx.tts.speak("I detected motion.")

        elif not motion_detected and self._motion_active:
            if time.monotonic() - self._last_motion_time > 3.0:
                self._motion_active = False
                await self.ctx.event_bus.emit("motion.cleared", source=self.name)

        self._prev_gray = gray

    async def cmd_start(self, text: str, user_name: str) -> Optional[str]:
        self._monitoring = True
        self._prev_gray = None
        return "Security monitoring activated. I'll alert you if I detect motion."

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        self._monitoring = False
        self._motion_active = False
        return "Security monitoring deactivated."

    async def cmd_status(self, text: str, user_name: str) -> Optional[str]:
        if not self._monitoring:
            return "Motion monitoring is off. Say 'start monitoring' to enable."
        if self._motion_active:
            return "Yes, I'm currently detecting motion!"
        return "All clear. No motion detected."
