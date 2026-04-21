"""Timer/Alarm skill — set timers and alarms with spoken alerts.

Voice commands: "set a timer for 5 minutes", "cancel timer", "time left"
Events emitted: timer.fired, alarm.fired
"""

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Dict, Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


@dataclass
class Timer:
    name: str
    duration: float
    end_time: float
    task: Optional[asyncio.Task] = None


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._timers: Dict[str, Timer] = {}
        self._counter = 0

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="timer_alarm",
            version="1.0.0",
            description="Set timers and alarms with spoken alerts",
            requires={"tts"},
            commands=[
                VoiceCommand(triggers=["set a timer", "timer for", "remind me in"], handler="cmd_set_timer"),
                VoiceCommand(triggers=["cancel timer", "stop timer", "delete timer"], handler="cmd_cancel"),
                VoiceCommand(triggers=["how much time", "time left", "timer status", "check timer"], handler="cmd_status"),
            ],
            emits_events=["timer.fired"],
        )

    def _parse_duration(self, text: str) -> Optional[float]:
        """Extract duration in seconds from text like '5 minutes', '30 seconds', '1 hour'."""
        text_lower = text.lower()
        total = 0.0
        found = False

        patterns = [
            (r"(\d+)\s*hour", 3600),
            (r"(\d+)\s*minute", 60),
            (r"(\d+)\s*second", 1),
        ]
        for pattern, multiplier in patterns:
            match = re.search(pattern, text_lower)
            if match:
                total += int(match.group(1)) * multiplier
                found = True

        # Handle bare numbers (assume minutes)
        if not found:
            match = re.search(r"(\d+)", text_lower)
            if match:
                total = int(match.group(1)) * 60
                found = True

        return total if found and total > 0 else None

    def _format_duration(self, seconds: float) -> str:
        """Format seconds as human-readable duration."""
        if seconds >= 3600:
            h = int(seconds // 3600)
            m = int((seconds % 3600) // 60)
            return f"{h} hour{'s' if h != 1 else ''} and {m} minute{'s' if m != 1 else ''}"
        elif seconds >= 60:
            m = int(seconds // 60)
            s = int(seconds % 60)
            if s:
                return f"{m} minute{'s' if m != 1 else ''} and {s} second{'s' if s != 1 else ''}"
            return f"{m} minute{'s' if m != 1 else ''}"
        else:
            return f"{int(seconds)} second{'s' if seconds != 1 else ''}"

    async def _timer_worker(self, timer_name: str, duration: float):
        """Wait for timer to expire, then announce."""
        await asyncio.sleep(duration)
        timer = self._timers.pop(timer_name, None)
        if timer:
            await self.ctx.event_bus.emit("timer.fired", source=self.name, name=timer_name)
            await self.ctx.tts.speak_and_wait(
                f"Time's up! Your {self._format_duration(duration)} timer is done."
            )

    async def cmd_set_timer(self, text: str, user_name: str) -> Optional[str]:
        duration = self._parse_duration(text)
        if duration is None:
            return "I couldn't understand the duration. Try something like 'set a timer for 5 minutes'."

        self._counter += 1
        name = f"timer_{self._counter}"
        end_time = time.monotonic() + duration
        timer = Timer(name=name, duration=duration, end_time=end_time)
        timer.task = self._spawn_task(self._timer_worker(name, duration))
        self._timers[name] = timer

        return f"Timer set for {self._format_duration(duration)}."

    async def cmd_cancel(self, text: str, user_name: str) -> Optional[str]:
        if not self._timers:
            return "No timers are running."
        # Cancel the most recent timer
        name = list(self._timers.keys())[-1]
        timer = self._timers.pop(name)
        if timer.task:
            timer.task.cancel()
        return "Timer cancelled."

    async def cmd_status(self, text: str, user_name: str) -> Optional[str]:
        if not self._timers:
            return "No timers are running."
        parts = []
        now = time.monotonic()
        for name, timer in self._timers.items():
            remaining = max(0, timer.end_time - now)
            parts.append(self._format_duration(remaining) + " left")
        return f"Active timers: {', '.join(parts)}."
