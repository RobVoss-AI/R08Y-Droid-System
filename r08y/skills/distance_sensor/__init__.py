"""Ultrasonic distance reporting through R08Y's sensor abstraction."""

import asyncio
from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._monitoring = False
        self._latest_distance: Optional[float] = None
        self._approach_threshold = 50.0
        self._someone_close = False

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="distance_sensor",
            version="2.0.0",
            description="Freenove ultrasonic proximity detection",
            requires={"distance"},
            commands=[
                VoiceCommand(triggers=["how far", "what's the distance", "measure distance"], handler="cmd_measure"),
                VoiceCommand(triggers=["start proximity", "proximity alert", "watch distance"], handler="cmd_start"),
                VoiceCommand(triggers=["stop proximity", "stop distance"], handler="cmd_stop"),
            ],
            emits_events=["distance.approach", "distance.clear"],
        )

    async def setup(self, context):
        await super().setup(context)
        self._approach_threshold = float(self.ctx.config.get("approach_threshold", 50.0))

    async def _monitor_loop(self):
        while self._monitoring and self._running:
            distance = await self.ctx.distance.read_cm()
            if distance is not None:
                self._latest_distance = distance
                if distance < self._approach_threshold and not self._someone_close:
                    self._someone_close = True
                    await self.ctx.event_bus.emit(
                        "distance.approach", source=self.name, distance_cm=distance
                    )
                elif distance >= self._approach_threshold and self._someone_close:
                    self._someone_close = False
                    await self.ctx.event_bus.emit(
                        "distance.clear", source=self.name, distance_cm=distance
                    )
            await asyncio.sleep(0.2)

    async def cmd_measure(self, text: str, user_name: str) -> Optional[str]:
        distance = await self.ctx.distance.read_cm()
        if distance is None:
            return "I couldn't get a distance reading. Check the ultrasonic sensor."
        return f"The nearest object is {distance:.1f} centimeters away."

    async def cmd_start(self, text: str, user_name: str) -> Optional[str]:
        if self._monitoring:
            return "Proximity monitoring is already active."
        self._monitoring = True
        self._spawn_task(self._monitor_loop())
        return (
            "Proximity monitoring active. I'll alert you when something is within "
            f"{self._approach_threshold:.0f} centimeters."
        )

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        self._monitoring = False
        return "Proximity monitoring stopped."

    async def stop(self):
        self._monitoring = False
        await super().stop()
