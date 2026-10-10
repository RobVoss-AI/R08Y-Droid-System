"""Greeter skill — R08Y announces and welcomes recognized people.

Watches the vision pipeline for face recognition events. When a known person
appears (or a new unknown person), R08Y greets them by name with a personalized
welcome. Configurable cooldown so it doesn't greet the same person every frame.

Events emitted: greeter.welcomed
"""

import time
from typing import Dict, Optional

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._enabled = True
        self._last_greeted: Dict[str, float] = {}  # name → timestamp
        self._cooldown = 300.0  # Don't re-greet same person within 5 minutes
        self._greet_unknown = False
        self._announced_unknown = False
        self._last_unknown_time: float = 0

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="greeter",
            version="1.0.0",
            description="Welcome recognized people by name when they appear",
            requires={"camera", "tts"},
            pipeline_hooks=[PipelineHook.VISION_DETECTIONS],
            commands=[
                VoiceCommand(triggers=["greetings on", "start greeting", "enable greetings"], handler="cmd_enable"),
                VoiceCommand(triggers=["greetings off", "stop greeting", "disable greetings"], handler="cmd_disable"),
                VoiceCommand(triggers=["greet everyone", "greet strangers"], handler="cmd_greet_all"),
                VoiceCommand(triggers=["greet known only", "only greet friends"], handler="cmd_known_only"),
            ],
            emits_events=["greeter.welcomed"],
        )

    async def setup(self, context):
        await super().setup(context)
        self._cooldown = self.ctx.config.get("cooldown_seconds", 300)
        self._greet_unknown = self.ctx.config.get("greet_unknown", False)

    async def on_vision_detections(self, frame, detections: list) -> None:
        """Check if we should greet anyone visible."""
        if not self._enabled or not detections:
            return

        # Don't greet while speaking or listening
        if self.ctx.state.is_speaking or self.ctx.state.is_listening:
            return

        recognized = self.ctx.state.recognized
        now = time.monotonic()

        if recognized and recognized.is_known:
            name = recognized.name
            last = self._last_greeted.get(name, 0)
            if now - last > self._cooldown:
                self._last_greeted[name] = now
                greeting = self._build_greeting(name)
                await self.ctx.tts.speak(greeting)
                await self._wave_gesture()
                await self.ctx.event_bus.emit(
                    "greeter.welcomed", source=self.name,
                    name=name, is_known=True,
                )

        elif self._greet_unknown and detections:
            if now - self._last_unknown_time > self._cooldown:
                self._last_unknown_time = now
                await self.ctx.tts.speak("Hello there! I don't think we've met. Say 'remember my face' to introduce yourself.")
                await self.ctx.event_bus.emit(
                    "greeter.welcomed", source=self.name,
                    name="unknown", is_known=False,
                )

    def _build_greeting(self, name: str) -> str:
        """Generate a contextual greeting."""
        from datetime import datetime
        hour = datetime.now().hour
        if hour < 12:
            time_greeting = "Good morning"
        elif hour < 17:
            time_greeting = "Good afternoon"
        else:
            time_greeting = "Good evening"

        return f"{time_greeting}, {name}! Nice to see you."

    async def _wave_gesture(self) -> None:
        """Small nod as a greeting gesture."""
        await self.ctx.expression.perform_gesture("greet")

    async def cmd_enable(self, text: str, user_name: str) -> Optional[str]:
        self._enabled = True
        return "Greetings enabled. I'll welcome people when I see them."

    async def cmd_disable(self, text: str, user_name: str) -> Optional[str]:
        self._enabled = False
        return "Greetings disabled."

    async def cmd_greet_all(self, text: str, user_name: str) -> Optional[str]:
        self._greet_unknown = True
        return "I'll now greet everyone, including strangers."

    async def cmd_known_only(self, text: str, user_name: str) -> Optional[str]:
        self._greet_unknown = False
        return "I'll only greet people I recognize."
