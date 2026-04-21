"""Photo Capture skill — take and save photos on command.

Voice commands: "take a photo", "take a picture", "say cheese"
Events emitted: photo.captured
"""

import time
from pathlib import Path
from typing import Optional

import cv2

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._photo_count = 0

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="photo_capture",
            version="1.0.0",
            description="Capture and save photos on command",
            requires={"camera"},
            commands=[
                VoiceCommand(triggers=["take a photo", "take a picture", "say cheese", "snap a photo"], handler="cmd_capture"),
                VoiceCommand(triggers=["how many photos", "photo count"], handler="cmd_count"),
            ],
            emits_events=["photo.captured"],
        )

    async def cmd_capture(self, text: str, user_name: str) -> Optional[str]:
        frame = await self.ctx.camera.get_frame()
        if frame is None:
            return "I can't see anything right now."

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        filename = f"photo_{timestamp}.jpg"
        filepath = Path(self.ctx.data_dir) / filename
        cv2.imwrite(str(filepath), frame)
        self._photo_count += 1

        await self.ctx.event_bus.emit(
            "photo.captured", source=self.name,
            path=str(filepath), user=user_name,
        )
        self.ctx.logger.info("Photo saved: %s", filepath)
        return f"Photo saved! That's number {self._photo_count}."

    async def cmd_count(self, text: str, user_name: str) -> Optional[str]:
        photos = list(Path(self.ctx.data_dir).glob("photo_*.jpg"))
        return f"I've taken {len(photos)} photos total."
