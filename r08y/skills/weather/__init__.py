"""Weather skill — fetch and announce weather forecasts.

Uses the free Open-Meteo API (no API key required) for weather data.
Configure location in config.yaml under skills.weather.
"""

import json
from typing import Optional
from urllib.request import urlopen
from urllib.error import URLError

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._latitude = 37.7749   # Default: San Francisco
        self._longitude = -122.4194
        self._location_name = "your area"

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="weather",
            version="1.0.0",
            description="Fetch and announce weather using Open-Meteo API",
            requires={"network"},
            commands=[
                VoiceCommand(triggers=["what's the weather", "weather report", "weather forecast", "how's the weather"], handler="cmd_weather"),
                VoiceCommand(triggers=["what's the temperature", "how cold", "how warm", "temperature outside"], handler="cmd_temperature"),
            ],
        )

    async def setup(self, context):
        await super().setup(context)
        self._latitude = self.ctx.config.get("latitude", self._latitude)
        self._longitude = self.ctx.config.get("longitude", self._longitude)
        self._location_name = self.ctx.config.get("location_name", self._location_name)

    def _fetch_weather(self) -> Optional[dict]:
        """Fetch current weather from Open-Meteo (free, no API key)."""
        url = (
            f"https://api.open-meteo.com/v1/forecast?"
            f"latitude={self._latitude}&longitude={self._longitude}"
            f"&current=temperature_2m,relative_humidity_2m,wind_speed_10m,weather_code"
            f"&temperature_unit=fahrenheit&wind_speed_unit=mph"
        )
        try:
            with urlopen(url, timeout=10) as resp:
                return json.loads(resp.read())
        except (URLError, json.JSONDecodeError, OSError) as e:
            self.ctx.logger.error("Weather fetch failed: %s", e)
            return None

    @staticmethod
    def _weather_description(code: int) -> str:
        """Convert WMO weather code to human description."""
        descriptions = {
            0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
            45: "foggy", 48: "icy fog",
            51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
            61: "slight rain", 63: "moderate rain", 65: "heavy rain",
            71: "slight snow", 73: "moderate snow", 75: "heavy snow",
            80: "slight rain showers", 81: "moderate rain showers", 82: "violent rain showers",
            95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with heavy hail",
        }
        return descriptions.get(code, "unknown conditions")

    async def cmd_weather(self, text: str, user_name: str) -> Optional[str]:
        data = self._fetch_weather()
        if not data or "current" not in data:
            return "I couldn't get the weather right now. Check the network connection."

        current = data["current"]
        temp = current.get("temperature_2m", "?")
        humidity = current.get("relative_humidity_2m", "?")
        wind = current.get("wind_speed_10m", "?")
        code = current.get("weather_code", 0)
        desc = self._weather_description(code)

        return (
            f"Currently in {self._location_name}: {desc}, "
            f"{temp} degrees Fahrenheit, {humidity}% humidity, "
            f"wind at {wind} miles per hour."
        )

    async def cmd_temperature(self, text: str, user_name: str) -> Optional[str]:
        data = self._fetch_weather()
        if not data or "current" not in data:
            return "I couldn't check the temperature right now."
        temp = data["current"].get("temperature_2m", "?")
        return f"It's currently {temp} degrees Fahrenheit in {self._location_name}."
