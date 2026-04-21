"""Camera Gestures skill — named positions and expressive movements.

Voice commands: "nod yes", "shake no", "look left/right/up/down", "dance", "center"
Events emitted: gesture.started, gesture.completed
"""

import asyncio
from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


GESTURES = {
    "nod": [
        (None, -15), (None, 15), (None, -15), (None, 15), (None, 0),
    ],
    "shake": [
        (-20, None), (20, None), (-20, None), (20, None), (0, None),
    ],
    "dance": [
        (-30, -10), (30, 10), (-30, 10), (30, -10), (-15, -15), (15, 15), (0, 0),
    ],
    "alert": [
        (-45, 0), (45, 0), (-45, 0), (45, 0), (0, 0),
    ],
    "look_left": [(-40, None)],
    "look_right": [(40, None)],
    "look_up": [(None, -30)],
    "look_down": [(None, 25)],
}


class Skill(BaseSkill):
    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="camera_gestures",
            version="1.0.0",
            description="Named camera positions and expressive movements",
            requires={"servos"},
            commands=[
                VoiceCommand(triggers=["nod yes", "nod your head"], handler="cmd_nod"),
                VoiceCommand(triggers=["shake no", "shake your head"], handler="cmd_shake"),
                VoiceCommand(triggers=["do a dance", "dance"], handler="cmd_dance"),
                VoiceCommand(triggers=["look left"], handler="cmd_look_left"),
                VoiceCommand(triggers=["look right"], handler="cmd_look_right"),
                VoiceCommand(triggers=["look up"], handler="cmd_look_up"),
                VoiceCommand(triggers=["look down"], handler="cmd_look_down"),
                VoiceCommand(triggers=["center", "look at me", "face forward"], handler="cmd_center"),
                VoiceCommand(triggers=["alert mode", "alert spin"], handler="cmd_alert"),
                VoiceCommand(triggers=["what gestures", "list gestures"], handler="cmd_list"),
            ],
            emits_events=["gesture.started", "gesture.completed"],
        )

    async def _run_gesture(self, name: str) -> str:
        steps = GESTURES.get(name)
        if not steps:
            return f"I don't know the gesture '{name}'."

        await self.ctx.event_bus.emit("gesture.started", source=self.name, gesture=name)
        servo = self.ctx.servo
        sc = self.ctx.config  # servo channels from context
        pan_ch = 0
        tilt_ch = 1

        home_pan = await servo.get_angle(pan_ch)
        home_tilt = await servo.get_angle(tilt_ch)

        for pan_delta, tilt_delta in steps:
            if pan_delta is not None:
                await servo.set_angle(pan_ch, home_pan + pan_delta)
            if tilt_delta is not None:
                await servo.set_angle(tilt_ch, home_tilt + tilt_delta)
            await asyncio.sleep(0.25)

        await self.ctx.event_bus.emit("gesture.completed", source=self.name, gesture=name)
        return ""

    async def cmd_nod(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("nod")
        return None  # No speech needed

    async def cmd_shake(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("shake")
        return None

    async def cmd_dance(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("dance")
        return "How's that for moves?"

    async def cmd_look_left(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("look_left")
        return None

    async def cmd_look_right(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("look_right")
        return None

    async def cmd_look_up(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("look_up")
        return None

    async def cmd_look_down(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("look_down")
        return None

    async def cmd_center(self, text: str, user_name: str) -> Optional[str]:
        await self.ctx.servo.home()
        return None

    async def cmd_alert(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("alert")
        return "Area scanned!"

    async def cmd_list(self, text: str, user_name: str) -> Optional[str]:
        names = ", ".join(GESTURES.keys())
        return f"I know these gestures: {names}."
