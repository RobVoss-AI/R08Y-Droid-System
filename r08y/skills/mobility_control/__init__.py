"""Voice-controlled, timed mecanum movements with distance guarding."""

import asyncio
import re
from typing import Optional

from r08y.modules.mobility import MobilityError, MobilitySafetyError
from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._speed = 0.45
        self._duration = 0.6
        self._max_duration = 2.0
        self._movement_lock = asyncio.Lock()

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="mobility_control",
            version="1.0.0",
            description="Guarded mecanum movement commands",
            requires={"mobility"},
            commands=[
                VoiceCommand(triggers=["move forward", "go forward", "drive forward"], handler="cmd_forward"),
                VoiceCommand(triggers=["move backward", "go backward", "back up"], handler="cmd_backward"),
                VoiceCommand(triggers=["strafe left", "slide left"], handler="cmd_strafe_left"),
                VoiceCommand(triggers=["strafe right", "slide right"], handler="cmd_strafe_right"),
                VoiceCommand(triggers=["turn left", "rotate left"], handler="cmd_turn_left"),
                VoiceCommand(triggers=["turn right", "rotate right"], handler="cmd_turn_right"),
                VoiceCommand(triggers=["stop moving", "emergency stop", "stop wheels"], handler="cmd_stop"),
                VoiceCommand(triggers=["mobility demo", "show your moves"], handler="cmd_demo"),
            ],
            listens_events=["distance.approach"],
            emits_events=["mobility.started", "mobility.stopped", "mobility.blocked"],
        )

    async def setup(self, context):
        await super().setup(context)
        self._speed = float(self.ctx.config.get("speed", 0.45))
        self._duration = float(self.ctx.config.get("duration", 0.6))
        self._max_duration = float(self.ctx.config.get("max_duration", 2.0))

    def _requested_duration(self, text: str) -> float:
        match = re.search(r"(?:for\s+)?(\d+(?:\.\d+)?)\s*seconds?", text.lower())
        if not match:
            return self._duration
        return min(self._max_duration, max(0.1, float(match.group(1))))

    async def _move(self, text: str, forward: float, strafe: float, turn: float) -> Optional[str]:
        async with self._movement_lock:
            try:
                duration = self._requested_duration(text)
                await self.ctx.event_bus.emit(
                    "mobility.started", source=self.name,
                    forward=forward, strafe=strafe, turn=turn, duration=duration,
                )
                await self.ctx.mobility.move_for(
                    forward * self._speed,
                    strafe * self._speed,
                    turn * self._speed,
                    duration,
                )
                await self.ctx.event_bus.emit("mobility.stopped", source=self.name)
                return None
            except MobilitySafetyError as exc:
                await self.ctx.mobility.emergency_stop()
                await self.ctx.event_bus.emit("mobility.blocked", source=self.name, reason=str(exc))
                return str(exc)
            except MobilityError:
                await self.ctx.mobility.emergency_stop()
                return "My wheel controller isn't available."

    async def on_distance_approach(self, event) -> None:
        await self.ctx.mobility.emergency_stop()

    async def cmd_forward(self, text: str, user_name: str) -> Optional[str]:
        return await self._move(text, 1, 0, 0)

    async def cmd_backward(self, text: str, user_name: str) -> Optional[str]:
        return await self._move(text, -1, 0, 0)

    async def cmd_strafe_left(self, text: str, user_name: str) -> Optional[str]:
        return await self._move(text, 0, -1, 0)

    async def cmd_strafe_right(self, text: str, user_name: str) -> Optional[str]:
        return await self._move(text, 0, 1, 0)

    async def cmd_turn_left(self, text: str, user_name: str) -> Optional[str]:
        return await self._move(text, 0, 0, 1)

    async def cmd_turn_right(self, text: str, user_name: str) -> Optional[str]:
        return await self._move(text, 0, 0, -1)

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        await self.ctx.mobility.emergency_stop()
        await self.ctx.event_bus.emit("mobility.stopped", source=self.name)
        return "Wheels stopped."

    async def cmd_demo(self, text: str, user_name: str) -> Optional[str]:
        async with self._movement_lock:
            try:
                for forward, strafe, turn in (
                    (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1)
                ):
                    await self.ctx.mobility.move_for(
                        forward * self._speed,
                        strafe * self._speed,
                        turn * self._speed,
                        min(0.35, self._duration),
                    )
                    await asyncio.sleep(0.15)
                return "Mobility check complete."
            except MobilityError as exc:
                await self.ctx.mobility.emergency_stop()
                return str(exc)

    async def stop(self):
        await self.ctx.mobility.emergency_stop()
        await super().stop()
