"""Storyteller skill — R08Y tells stories with dramatic delivery.

Shorter and more narrative than presentations. Uses Claude to generate stories
with embedded stage directions that control pacing, volume, and camera movement.
Great for kids, parties, or just fun.

Voice commands:
  "tell me a story about [topic]"
  "tell a scary story"
  "tell a bedtime story"
"""

import asyncio
import re
from typing import List, Optional, Tuple

from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


# Moods map to TTS voice adjustments and camera behavior
MOODS = {
    "excited":    {"rate": "+15%", "volume": "+20%", "gesture": "nod"},
    "whisper":    {"rate": "-15%", "volume": "-20%", "gesture": "lean_in"},
    "dramatic":   {"rate": "-25%", "volume": "+10%", "gesture": "dramatic_pause"},
    "happy":      {"rate": "+5%",  "volume": "+10%", "gesture": "nod"},
    "sad":        {"rate": "-10%", "volume": "-10%", "gesture": "look_down"},
    "scary":      {"rate": "-20%", "volume": "-5%",  "gesture": "lean_in"},
    "normal":     {"rate": "+0%",  "volume": "+0%",  "gesture": "center"},
}

STORY_GESTURES = {
    "nod":            [(None, -10), (None, 10), (None, 0)],
    "lean_in":        [(0, 10)],
    "dramatic_pause": [(0, -8)],
    "look_down":      [(None, 15)],
    "center":         [(0, 0)],
    "scan":           [(-25, 0), (25, 0), (0, 0)],
}


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._telling = False
        self._client = None
        self._model = "claude-sonnet-5-5"
        self._original_rate: str = "+0%"
        self._original_volume: str = "+0%"

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="storyteller",
            version="1.0.0",
            description="Tell stories with dramatic delivery and camera movement",
            requires={"tts"},
            optional={"servos"},
            commands=[
                VoiceCommand(triggers=["tell me a story", "tell a story", "story about"], handler="cmd_story"),
                VoiceCommand(triggers=["tell a scary story", "scary story"], handler="cmd_scary"),
                VoiceCommand(triggers=["tell a bedtime story", "bedtime story"], handler="cmd_bedtime"),
                VoiceCommand(triggers=["tell a joke", "tell me a joke"], handler="cmd_joke"),
                VoiceCommand(triggers=["stop the story", "stop telling"], handler="cmd_stop"),
            ],
            emits_events=["story.started", "story.ended"],
        )

    async def setup(self, context):
        await super().setup(context)
        if ANTHROPIC_AVAILABLE:
            from r08y.config import load_config
            config = load_config()
            api_key = context.config.get("anthropic_api_key", config.ai.api_key)
            self._model = context.config.get("model", config.ai.model)
            if api_key and not api_key.startswith("YOUR_"):
                self._client = anthropic.AsyncAnthropic(api_key=api_key)

    def _extract_topic(self, text: str) -> str:
        text_lower = text.lower()
        for prefix in ["story about ", "a story about ", "me a story about "]:
            idx = text_lower.find(prefix)
            if idx >= 0:
                return text[idx + len(prefix):].strip().rstrip(".!?,")
        return ""

    async def _generate_story(self, topic: str, style: str = "fun") -> str:
        """Use Claude to generate a story with mood markers."""
        if not self._client:
            return f"Once upon a time, there was a curious droid named R08Y who wanted to tell you about {topic or 'an adventure'}. But today, my storytelling brain needs an API key to work properly."

        style_notes = {
            "fun": "Light, entertaining, with humor. 2-3 minutes when read aloud.",
            "scary": "Suspenseful and spooky but not terrifying. Build tension. 2-3 minutes.",
            "bedtime": "Gentle, calming, with a peaceful ending. Slow pacing. 2-3 minutes.",
            "joke": "A setup and punchline. Can be a short funny story. Under 1 minute.",
        }

        prompt = (
            f"Write a {style} story{f' about {topic}' if topic else ''} for spoken delivery.\n\n"
            f"Style: {style_notes.get(style, style_notes['fun'])}\n\n"
            f"Insert mood markers in the text to control delivery:\n"
            f"  {{excited}} — speed up, get louder\n"
            f"  {{whisper}} — slow down, get quiet\n"
            f"  {{dramatic}} — slow way down, slight pause\n"
            f"  {{happy}} — warm and upbeat\n"
            f"  {{sad}} — slower, softer\n"
            f"  {{scary}} — low and slow\n"
            f"  {{normal}} — return to normal\n"
            f"  {{pause}} — insert a 2-second dramatic pause\n\n"
            f"Use these naturally — a scary story should have {{whisper}} and {{dramatic}} moments.\n"
            f"Don't overuse them. Maybe 4-8 markers total.\n"
            f"Write ONLY the story text with markers. No titles or stage directions."
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except Exception as e:
            self.ctx.logger.error("Story generation failed: %s", e)
            return f"I wanted to tell you a story about {topic or 'something wonderful'}, but I'm having trouble coming up with one right now."

    def _parse_story(self, raw: str) -> List[Tuple[str, Optional[str]]]:
        """Split story into (text, mood) chunks based on mood markers."""
        chunks = []
        pattern = r"\{(excited|whisper|dramatic|happy|sad|scary|normal|pause)\}"
        parts = re.split(pattern, raw)

        current_mood = None
        for part in parts:
            part = part.strip()
            if not part:
                continue
            if part in MOODS or part == "pause":
                current_mood = part
            else:
                chunks.append((part, current_mood))
                current_mood = None

        if not chunks:
            chunks.append((raw, None))

        return chunks

    async def _perform_gesture(self, gesture_name: str) -> None:
        moves = STORY_GESTURES.get(gesture_name)
        if not moves or not self.ctx.servo.available:
            return
        pan_ch, tilt_ch = 0, 1
        home_pan = await self.ctx.servo.get_angle(pan_ch)
        home_tilt = await self.ctx.servo.get_angle(tilt_ch)
        for pan_delta, tilt_delta in moves:
            if pan_delta is not None:
                await self.ctx.servo.set_angle(pan_ch, home_pan + pan_delta)
            if tilt_delta is not None:
                await self.ctx.servo.set_angle(tilt_ch, home_tilt + tilt_delta)
            await asyncio.sleep(0.3)

    async def _deliver_story(self, chunks: List[Tuple[str, Optional[str]]]) -> None:
        """Deliver the story with mood-driven voice and movement changes."""
        self._original_rate = self.ctx.tts._config.rate
        self._original_volume = self.ctx.tts._config.volume
        self._telling = True

        await self.ctx.event_bus.emit("story.started", source=self.name)

        for text, mood in chunks:
            if not self._telling:
                break

            if mood == "pause":
                await asyncio.sleep(2.0)
                continue

            if mood and mood in MOODS:
                settings = MOODS[mood]
                self.ctx.tts._config.rate = settings["rate"]
                self.ctx.tts._config.volume = settings["volume"]
                await self._perform_gesture(settings["gesture"])

            await self.ctx.tts.speak_and_wait(text)

        # Restore
        self.ctx.tts._config.rate = self._original_rate
        self.ctx.tts._config.volume = self._original_volume
        self._telling = False
        if self.ctx.servo.available:
            await self.ctx.servo.home()
        await self.ctx.event_bus.emit("story.ended", source=self.name)

    async def _start_story(self, topic: str, style: str) -> Optional[str]:
        if self._telling:
            return "I'm already telling a story. Say 'stop the story' first."

        await self.ctx.tts.speak_and_wait("Let me think of a good one...")
        raw = await self._generate_story(topic, style)
        chunks = self._parse_story(raw)
        self._spawn_task(self._deliver_story(chunks))
        return None

    async def cmd_story(self, text: str, user_name: str) -> Optional[str]:
        topic = self._extract_topic(text)
        return await self._start_story(topic, "fun")

    async def cmd_scary(self, text: str, user_name: str) -> Optional[str]:
        topic = self._extract_topic(text)
        return await self._start_story(topic or "a haunted house", "scary")

    async def cmd_bedtime(self, text: str, user_name: str) -> Optional[str]:
        topic = self._extract_topic(text)
        return await self._start_story(topic or "a sleepy adventure", "bedtime")

    async def cmd_joke(self, text: str, user_name: str) -> Optional[str]:
        topic = self._extract_topic(text)
        return await self._start_story(topic, "joke")

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        if not self._telling:
            return "I'm not telling a story right now."
        self._telling = False
        self.ctx.tts.interrupt()
        self.ctx.tts._config.rate = self._original_rate
        self.ctx.tts._config.volume = self._original_volume
        return "Story time's over."

    async def stop(self):
        self._telling = False
        if self.ctx and self.ctx.tts:
            self.ctx.tts._config.rate = self._original_rate
            self.ctx.tts._config.volume = self._original_volume
        await super().stop()
