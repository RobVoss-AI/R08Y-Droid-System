"""LED Controller skill — NeoPixel/WS2812 LED ring for status indication.

Provides mood lighting, status animations (listening, thinking, speaking),
and event-driven reactions. Connects via GPIO 18 (PWM) by default.

Events listened: motion.detected, emotion.detected, system.overheat
"""

import asyncio
from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand
from r08y.skills._bus import Event

try:
    import board
    import neopixel
    NEOPIXEL_AVAILABLE = True
except ImportError:
    NEOPIXEL_AVAILABLE = False


# Color presets (R, G, B)
COLORS = {
    "off":      (0, 0, 0),
    "white":    (255, 255, 255),
    "red":      (255, 0, 0),
    "green":    (0, 255, 0),
    "blue":     (0, 0, 255),
    "yellow":   (255, 200, 0),
    "purple":   (128, 0, 255),
    "orange":   (255, 80, 0),
    "cyan":     (0, 255, 255),
    "warm":     (255, 140, 40),
}

# Status animation patterns
STATUS_PATTERNS = {
    "listening": {"color": (0, 100, 255), "mode": "pulse"},
    "thinking":  {"color": (255, 200, 0),  "mode": "spin"},
    "speaking":  {"color": (0, 255, 100),  "mode": "pulse"},
    "alert":     {"color": (255, 0, 0),    "mode": "flash"},
    "idle":      {"color": (20, 20, 40),   "mode": "static"},
}


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._pixels = None
        self._num_pixels = 12
        self._brightness = 0.3
        self._current_pattern: Optional[str] = None
        self._animation_task: Optional[asyncio.Task] = None

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="led_controller",
            version="1.0.0",
            description="NeoPixel LED ring for status and mood lighting",
            requires={"gpio"},
            commands=[
                VoiceCommand(triggers=["set color", "change color", "light color"], handler="cmd_color"),
                VoiceCommand(triggers=["lights off", "leds off", "turn off lights"], handler="cmd_off"),
                VoiceCommand(triggers=["lights on", "leds on"], handler="cmd_on"),
                VoiceCommand(triggers=["brightness"], handler="cmd_brightness"),
                VoiceCommand(triggers=["what colors", "list colors"], handler="cmd_list"),
            ],
            listens_events=["motion.detected", "system.overheat", "emotion.detected"],
        )

    async def setup(self, context):
        await super().setup(context)
        self._num_pixels = self.ctx.config.get("num_pixels", 12)
        self._brightness = self.ctx.config.get("brightness", 0.3)

    async def start(self):
        await super().start()
        if not NEOPIXEL_AVAILABLE:
            self.ctx.logger.info("NeoPixel not available — LED controller uses mock mode")
            return
        try:
            self._pixels = neopixel.NeoPixel(
                board.D18,
                self._num_pixels,
                brightness=self._brightness,
                auto_write=False,
            )
            self._set_all(COLORS["warm"])
            self.ctx.logger.info("LED controller initialized (%d pixels)", self._num_pixels)
        except Exception:
            self.ctx.logger.exception("Failed to initialize NeoPixel")

    def _set_all(self, color: tuple) -> None:
        if self._pixels:
            self._pixels.fill(color)
            self._pixels.show()

    def _set_pixel(self, index: int, color: tuple) -> None:
        if self._pixels and 0 <= index < self._num_pixels:
            self._pixels[index] = color
            self._pixels.show()

    async def _animate_pulse(self, color: tuple):
        """Pulsing brightness animation."""
        import math
        step = 0
        while self._running:
            brightness = (math.sin(step * 0.1) + 1) / 2
            dimmed = tuple(int(c * brightness) for c in color)
            self._set_all(dimmed)
            step += 1
            await asyncio.sleep(0.05)

    async def _animate_spin(self, color: tuple):
        """Spinning dot animation."""
        pos = 0
        while self._running:
            if self._pixels:
                self._pixels.fill((0, 0, 0))
                self._pixels[pos % self._num_pixels] = color
                self._pixels[(pos + 1) % self._num_pixels] = tuple(c // 3 for c in color)
                self._pixels.show()
            pos += 1
            await asyncio.sleep(0.08)

    async def _animate_flash(self, color: tuple):
        """Flashing alert animation."""
        on = True
        for _ in range(10):
            if not self._running:
                break
            self._set_all(color if on else (0, 0, 0))
            on = not on
            await asyncio.sleep(0.2)
        self._set_all(COLORS.get("warm", (20, 20, 40)))

    async def set_status(self, status: str) -> None:
        """Set LED status animation (called by main orchestrator or events)."""
        pattern = STATUS_PATTERNS.get(status)
        if not pattern:
            return
        if self._animation_task:
            self._animation_task.cancel()
        color = pattern["color"]
        mode = pattern["mode"]
        if mode == "static":
            self._set_all(color)
        elif mode == "pulse":
            self._animation_task = self._spawn_task(self._animate_pulse(color))
        elif mode == "spin":
            self._animation_task = self._spawn_task(self._animate_spin(color))
        elif mode == "flash":
            self._animation_task = self._spawn_task(self._animate_flash(color))

    async def on_motion_detected(self, event: Event) -> None:
        await self.set_status("alert")

    async def on_system_overheat(self, event: Event) -> None:
        self._set_all(COLORS["red"])

    async def on_emotion_detected(self, event: Event) -> None:
        emotion = event.payload.get("emotion", "")
        emotion_colors = {
            "happy": COLORS["yellow"],
            "sad": COLORS["blue"],
            "surprised": COLORS["purple"],
            "neutral": COLORS["warm"],
        }
        color = emotion_colors.get(emotion, COLORS["warm"])
        self._set_all(color)

    async def cmd_color(self, text: str, user_name: str) -> Optional[str]:
        text_lower = text.lower()
        for name, color in COLORS.items():
            if name in text_lower and name != "off":
                self._set_all(color)
                return f"Color set to {name}."
        return f"I don't know that color. Try: {', '.join(c for c in COLORS if c != 'off')}."

    async def cmd_off(self, text: str, user_name: str) -> Optional[str]:
        if self._animation_task:
            self._animation_task.cancel()
        self._set_all(COLORS["off"])
        return "Lights off."

    async def cmd_on(self, text: str, user_name: str) -> Optional[str]:
        self._set_all(COLORS["warm"])
        return "Lights on."

    async def cmd_brightness(self, text: str, user_name: str) -> Optional[str]:
        import re
        match = re.search(r"(\d+)", text)
        if match:
            val = min(100, max(1, int(match.group(1))))
            self._brightness = val / 100.0
            if self._pixels:
                self._pixels.brightness = self._brightness
                self._pixels.show()
            return f"Brightness set to {val}%."
        return "Say a brightness level from 1 to 100."

    async def cmd_list(self, text: str, user_name: str) -> Optional[str]:
        names = ", ".join(c for c in COLORS if c != "off")
        return f"Available colors: {names}."

    async def stop(self):
        if self._animation_task:
            self._animation_task.cancel()
        self._set_all((0, 0, 0))
        await super().stop()
