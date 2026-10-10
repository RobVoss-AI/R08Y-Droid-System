"""Camera Gestures skill — named positions and expressive movements.

Voice commands: "nod yes", "shake no", "look left/right/up/down", "dance", "center"
Events emitted: gesture.started, gesture.completed
"""

from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


GESTURES = (
    "nod", "shake", "dance", "alert",
    "look_left", "look_right", "look_up", "look_down",
)


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
        if name not in GESTURES:
            return f"I don't know the gesture '{name}'."

        await self.ctx.event_bus.emit("gesture.started", source=self.name, gesture=name)
        completed = await self.ctx.expression.perform_gesture(name)

        if completed:
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
        await self.ctx.expression.home()
        return None

    async def cmd_alert(self, text: str, user_name: str) -> Optional[str]:
        await self._run_gesture("alert")
        return "Area scanned!"

    async def cmd_list(self, text: str, user_name: str) -> Optional[str]:
        names = ", ".join(GESTURES)
        return f"I know these gestures: {names}."
