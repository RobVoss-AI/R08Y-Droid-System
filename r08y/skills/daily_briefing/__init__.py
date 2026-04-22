"""Daily Briefing skill — morning/evening summary of weather, time, and news.

Aggregates data from other skills (weather, system_monitor, timer_alarm) and
adds a time greeting. Can be triggered by voice or scheduled.

Voice commands: "good morning", "daily briefing", "what did I miss"
"""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._latitude = 37.7749
        self._longitude = -122.4194
        self._location_name = "your area"

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="daily_briefing",
            version="1.0.0",
            description="Morning/evening summary with weather, time, and system status",
            requires={"tts"},
            optional={"network"},
            commands=[
                VoiceCommand(triggers=["good morning", "morning briefing"], handler="cmd_morning"),
                VoiceCommand(triggers=["good evening", "evening briefing"], handler="cmd_evening"),
                VoiceCommand(triggers=["daily briefing", "what did i miss", "catch me up", "brief me"], handler="cmd_briefing"),
            ],
        )

    async def setup(self, context):
        await super().setup(context)
        self._latitude = self.ctx.config.get("latitude", self._latitude)
        self._longitude = self.ctx.config.get("longitude", self._longitude)
        self._location_name = self.ctx.config.get("location_name", self._location_name)

    def _get_greeting(self) -> str:
        hour = datetime.now().hour
        if hour < 12:
            return "Good morning"
        elif hour < 17:
            return "Good afternoon"
        else:
            return "Good evening"

    def _get_time_summary(self) -> str:
        now = datetime.now()
        day = now.strftime("%A, %B %d")
        time_str = now.strftime("%I:%M %p").lstrip("0")
        return f"It's {time_str} on {day}."

    def _fetch_weather_summary(self) -> Optional[str]:
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={self._latitude}&longitude={self._longitude}"
            f"&current=temperature_2m,weather_code"
            f"&daily=temperature_2m_max,temperature_2m_min,weather_code"
            f"&temperature_unit=fahrenheit&timezone=auto&forecast_days=1"
        )
        try:
            with urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
            current = data.get("current", {})
            daily = data.get("daily", {})
            temp = current.get("temperature_2m", "?")
            high = daily.get("temperature_2m_max", [None])[0]
            low = daily.get("temperature_2m_min", [None])[0]

            weather_codes = {
                0: "clear skies", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
                45: "fog", 51: "light drizzle", 61: "rain", 71: "snow",
                80: "rain showers", 95: "thunderstorms",
            }
            code = current.get("weather_code", 0)
            desc = weather_codes.get(code, "mixed conditions")

            parts = [f"Currently {temp} degrees with {desc} in {self._location_name}."]
            if high is not None and low is not None:
                parts.append(f"Today's high is {high}, low is {low}.")
            return " ".join(parts)
        except (URLError, json.JSONDecodeError, OSError, KeyError):
            return None

    def _get_system_summary(self) -> Optional[str]:
        """Quick system health check."""
        try:
            thermal = Path("/sys/class/thermal/thermal_zone0/temp")
            if thermal.exists():
                temp = int(thermal.read_text().strip()) / 1000.0
                if temp > 70:
                    return f"A heads up — I'm running warm at {temp:.0f} degrees Celsius."
                return f"I'm running cool at {temp:.0f} degrees."
        except (ValueError, OSError):
            pass
        return None

    async def _build_briefing(self, user_name: str, time_of_day: str = "") -> str:
        """Assemble the full briefing."""
        greeting = self._get_greeting()
        name_part = f", {user_name}" if user_name != "unknown" else ""
        parts = [f"{greeting}{name_part}!"]

        # Time
        parts.append(self._get_time_summary())

        # Weather
        weather = self._fetch_weather_summary()
        if weather:
            parts.append(weather)
        else:
            parts.append("I couldn't check the weather right now.")

        # System health (keep it brief)
        sys_summary = self._get_system_summary()
        if sys_summary:
            parts.append(sys_summary)

        # Closing
        if time_of_day == "morning":
            parts.append("Have a great day!")
        elif time_of_day == "evening":
            parts.append("Hope you had a good day.")
        else:
            parts.append("That's your briefing.")

        return " ".join(parts)

    async def cmd_morning(self, text: str, user_name: str) -> Optional[str]:
        return await self._build_briefing(user_name, "morning")

    async def cmd_evening(self, text: str, user_name: str) -> Optional[str]:
        return await self._build_briefing(user_name, "evening")

    async def cmd_briefing(self, text: str, user_name: str) -> Optional[str]:
        return await self._build_briefing(user_name)
