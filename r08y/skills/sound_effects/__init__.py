"""Sound Effects skill — play audio cues for events and on command.

Plays startup chimes, alert beeps, acknowledgment sounds, etc.
Can be triggered by voice or by events from other skills.
"""

import asyncio
import math
import struct
import wave
import io
from typing import Optional

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand
from r08y.skills._bus import Event


SOUNDS = {
    "beep":    {"freq": 800,  "duration": 0.15, "repeat": 1},
    "success": {"freq": 1200, "duration": 0.1,  "repeat": 2},
    "error":   {"freq": 300,  "duration": 0.3,  "repeat": 1},
    "alert":   {"freq": 1000, "duration": 0.1,  "repeat": 4},
    "happy":   {"freq": 880,  "duration": 0.08, "repeat": 3},
    "sad":     {"freq": 220,  "duration": 0.5,  "repeat": 1},
    "startup": {"freq": 660,  "duration": 0.1,  "repeat": 3},
    "ack":     {"freq": 1000, "duration": 0.05, "repeat": 1},
}


def _generate_wav(freq: int, duration: float) -> bytes:
    """Generate a sine wave tone as WAV bytes (pure Python, no shell)."""
    sample_rate = 22050
    n_samples = int(sample_rate * duration)
    samples = [int(32767 * math.sin(2 * math.pi * freq * t / sample_rate)) for t in range(n_samples)]
    raw = struct.pack(f"<{n_samples}h", *samples)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(raw)
    return buf.getvalue()


class Skill(BaseSkill):
    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="sound_effects",
            version="1.0.0",
            description="Play audio cues for events and on command",
            requires=set(),
            commands=[
                VoiceCommand(triggers=["play a beep", "beep"], handler="cmd_beep"),
                VoiceCommand(triggers=["happy sound", "play happy"], handler="cmd_happy"),
                VoiceCommand(triggers=["sad sound", "play sad"], handler="cmd_sad"),
                VoiceCommand(triggers=["alert sound", "play alert"], handler="cmd_alert"),
                VoiceCommand(triggers=["what sounds", "list sounds"], handler="cmd_list"),
            ],
            listens_events=["motion.detected", "timer.fired", "system.overheat"],
        )

    async def _play_tone(self, name: str) -> None:
        """Generate and play a tone."""
        sound = SOUNDS.get(name)
        if not sound:
            return
        wav_data = _generate_wav(sound["freq"], sound["duration"])
        for i in range(sound["repeat"]):
            try:
                proc = await asyncio.create_subprocess_exec(
                    "aplay", "-q", "-",
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.communicate(input=wav_data)
            except FileNotFoundError:
                self.ctx.logger.debug("aplay not available for sound effects")
                return
            if i < sound["repeat"] - 1:
                await asyncio.sleep(0.08)

    async def on_motion_detected(self, event: Event) -> None:
        await self._play_tone("alert")

    async def on_timer_fired(self, event: Event) -> None:
        await self._play_tone("alert")

    async def on_system_overheat(self, event: Event) -> None:
        await self._play_tone("error")

    async def cmd_beep(self, text: str, user_name: str) -> Optional[str]:
        await self._play_tone("beep")
        return None

    async def cmd_happy(self, text: str, user_name: str) -> Optional[str]:
        await self._play_tone("happy")
        return None

    async def cmd_sad(self, text: str, user_name: str) -> Optional[str]:
        await self._play_tone("sad")
        return None

    async def cmd_alert(self, text: str, user_name: str) -> Optional[str]:
        await self._play_tone("alert")
        return None

    async def cmd_list(self, text: str, user_name: str) -> Optional[str]:
        return f"Available sounds: {', '.join(SOUNDS.keys())}."
