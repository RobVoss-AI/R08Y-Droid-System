"""Distance Sensor skill — HC-SR04 ultrasonic proximity detection.

Wiring: TRIG → GPIO 23, ECHO → GPIO 24 (with voltage divider for 3.3V).
Emits events when someone approaches or moves away. Required for future
obstacle avoidance when mobility is added.

Events emitted: distance.approach, distance.clear
"""

import asyncio
import time
from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand

# GPIO is only available on Pi
try:
    import RPi.GPIO as GPIO
    GPIO_AVAILABLE = True
except ImportError:
    GPIO_AVAILABLE = False


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._monitoring = False
        self._latest_distance: Optional[float] = None
        self._approach_threshold = 50.0  # cm
        self._someone_close = False
        self._trig_pin = 23
        self._echo_pin = 24

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="distance_sensor",
            version="1.0.0",
            description="HC-SR04 ultrasonic proximity detection",
            requires={"gpio"},
            commands=[
                VoiceCommand(triggers=["how far", "what's the distance", "measure distance"], handler="cmd_measure"),
                VoiceCommand(triggers=["start proximity", "proximity alert", "watch distance"], handler="cmd_start"),
                VoiceCommand(triggers=["stop proximity", "stop distance"], handler="cmd_stop"),
            ],
            emits_events=["distance.approach", "distance.clear"],
        )

    async def setup(self, context):
        await super().setup(context)
        self._trig_pin = self.ctx.config.get("trig_pin", 23)
        self._echo_pin = self.ctx.config.get("echo_pin", 24)
        self._approach_threshold = self.ctx.config.get("approach_threshold", 50.0)

    async def start(self):
        await super().start()
        if not GPIO_AVAILABLE:
            self.ctx.logger.info("GPIO not available — distance sensor disabled")
            return
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setup(self._trig_pin, GPIO.OUT)
            GPIO.setup(self._echo_pin, GPIO.IN)
            GPIO.output(self._trig_pin, False)
            self.ctx.logger.info("Distance sensor initialized (TRIG=%d, ECHO=%d)", self._trig_pin, self._echo_pin)
        except Exception:
            self.ctx.logger.exception("Failed to initialize distance sensor GPIO")

    def _measure_sync(self) -> Optional[float]:
        """Measure distance in cm (blocking, ~60ms)."""
        if not GPIO_AVAILABLE:
            return None
        try:
            GPIO.output(self._trig_pin, True)
            time.sleep(0.00001)
            GPIO.output(self._trig_pin, False)

            start = time.monotonic()
            timeout = start + 0.1
            while GPIO.input(self._echo_pin) == 0:
                start = time.monotonic()
                if start > timeout:
                    return None
            while GPIO.input(self._echo_pin) == 1:
                end = time.monotonic()
                if end > timeout:
                    return None

            duration = end - start
            distance = (duration * 34300) / 2  # Speed of sound / 2
            return round(distance, 1) if distance < 400 else None
        except Exception:
            return None

    async def _monitor_loop(self):
        """Continuously monitor distance and emit events."""
        loop = asyncio.get_event_loop()
        while self._monitoring and self._running:
            dist = await loop.run_in_executor(None, self._measure_sync)
            if dist is not None:
                self._latest_distance = dist
                if dist < self._approach_threshold and not self._someone_close:
                    self._someone_close = True
                    await self.ctx.event_bus.emit("distance.approach", source=self.name, distance_cm=dist)
                elif dist >= self._approach_threshold and self._someone_close:
                    self._someone_close = False
                    await self.ctx.event_bus.emit("distance.clear", source=self.name, distance_cm=dist)
            await asyncio.sleep(0.2)

    async def cmd_measure(self, text: str, user_name: str) -> Optional[str]:
        if not GPIO_AVAILABLE:
            return "Distance sensor not available on this system."
        loop = asyncio.get_event_loop()
        dist = await loop.run_in_executor(None, self._measure_sync)
        if dist is None:
            return "I couldn't get a reading. Check the sensor wiring."
        return f"Distance: {dist} centimeters."

    async def cmd_start(self, text: str, user_name: str) -> Optional[str]:
        if not GPIO_AVAILABLE:
            return "Distance sensor not available."
        self._monitoring = True
        self._spawn_task(self._monitor_loop())
        return f"Proximity monitoring active. I'll alert when something is within {self._approach_threshold} centimeters."

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        self._monitoring = False
        return "Proximity monitoring stopped."

    async def stop(self):
        self._monitoring = False
        await super().stop()
        if GPIO_AVAILABLE:
            try:
                GPIO.cleanup([self._trig_pin, self._echo_pin])
            except Exception:
                pass
