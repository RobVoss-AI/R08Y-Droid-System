"""System Monitor skill — report Pi system health stats.

Voice commands: "how are you feeling", "system status", "what's your temperature"
Events emitted: system.overheat
"""

import asyncio
import os
import time
from pathlib import Path
from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._overheat_threshold = 80.0  # Celsius

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="system_monitor",
            version="1.0.0",
            description="Report Pi system health — CPU temp, memory, disk, uptime",
            requires=set(),
            commands=[
                VoiceCommand(triggers=["how are you feeling", "system status", "how's your health", "diagnostics", "run diagnostics"], handler="cmd_status"),
                VoiceCommand(triggers=["what's your temperature", "how hot are you", "cpu temp"], handler="cmd_temp"),
                VoiceCommand(triggers=["how much memory", "memory usage", "ram usage"], handler="cmd_memory"),
                VoiceCommand(triggers=["how long have you been running", "uptime"], handler="cmd_uptime"),
            ],
            emits_events=["system.overheat"],
        )

    async def start(self):
        await super().start()
        self._spawn_task(self._health_monitor_loop())

    async def _health_monitor_loop(self):
        """Periodically check system health, emit events if critical."""
        while self._running:
            temp = self._read_cpu_temp()
            if temp is not None and temp > self._overheat_threshold:
                await self.ctx.event_bus.emit("system.overheat", source=self.name, temp_c=temp)
                self.ctx.logger.warning("CPU temperature critical: %.1f°C", temp)
            await asyncio.sleep(30)

    @staticmethod
    def _read_cpu_temp() -> Optional[float]:
        """Read CPU temperature (Pi-specific)."""
        thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
        if thermal_path.exists():
            try:
                return int(thermal_path.read_text().strip()) / 1000.0
            except (ValueError, OSError):
                pass
        return None

    @staticmethod
    def _read_memory() -> dict:
        """Read memory usage."""
        try:
            with open("/proc/meminfo") as f:
                info = {}
                for line in f:
                    parts = line.split()
                    if parts[0] in ("MemTotal:", "MemAvailable:"):
                        info[parts[0].rstrip(":")] = int(parts[1])
            total = info.get("MemTotal", 0)
            avail = info.get("MemAvailable", 0)
            used_pct = ((total - avail) / total * 100) if total > 0 else 0
            return {"total_mb": total // 1024, "used_pct": used_pct}
        except (FileNotFoundError, KeyError):
            return {"total_mb": 0, "used_pct": 0}

    @staticmethod
    def _read_disk() -> float:
        """Read disk usage percentage."""
        try:
            stat = os.statvfs("/")
            total = stat.f_blocks * stat.f_frsize
            free = stat.f_bfree * stat.f_frsize
            return ((total - free) / total * 100) if total > 0 else 0
        except OSError:
            return 0

    @staticmethod
    def _read_uptime() -> str:
        """Read system uptime as human-readable string."""
        try:
            with open("/proc/uptime") as f:
                seconds = float(f.read().split()[0])
            days = int(seconds // 86400)
            hours = int((seconds % 86400) // 3600)
            minutes = int((seconds % 3600) // 60)
            parts = []
            if days:
                parts.append(f"{days} day{'s' if days != 1 else ''}")
            if hours:
                parts.append(f"{hours} hour{'s' if hours != 1 else ''}")
            parts.append(f"{minutes} minute{'s' if minutes != 1 else ''}")
            return ", ".join(parts)
        except (FileNotFoundError, ValueError):
            return "unknown"

    async def cmd_status(self, text: str, user_name: str) -> Optional[str]:
        temp = self._read_cpu_temp()
        mem = self._read_memory()
        disk = self._read_disk()
        uptime = self._read_uptime()

        temp_str = f"{temp:.0f} degrees" if temp else "temperature unknown"
        return (
            f"I'm running at {temp_str}, using {mem['used_pct']:.0f}% memory, "
            f"{disk:.0f}% disk. Been up for {uptime}."
        )

    async def cmd_temp(self, text: str, user_name: str) -> Optional[str]:
        temp = self._read_cpu_temp()
        if temp is None:
            return "I can't read my temperature on this system."
        if temp > 70:
            return f"I'm running hot at {temp:.1f} degrees Celsius!"
        return f"My CPU is at {temp:.1f} degrees Celsius. Feeling good."

    async def cmd_memory(self, text: str, user_name: str) -> Optional[str]:
        mem = self._read_memory()
        return f"Using {mem['used_pct']:.0f}% of {mem['total_mb']} megabytes of RAM."

    async def cmd_uptime(self, text: str, user_name: str) -> Optional[str]:
        return f"I've been running for {self._read_uptime()}."
