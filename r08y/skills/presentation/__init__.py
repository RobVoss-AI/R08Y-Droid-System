"""Presentation skill — R08Y delivers a timed talk on any topic.

Uses Claude to generate a structured multi-segment presentation, boosts TTS
volume for projection, and choreographs camera movements between segments
(scan audience, emphatic nods, look at different sides of the room).

Voice commands:
  "give a presentation about [topic]"
  "present for [N] minutes about [topic]"
  "stop presenting"

Events emitted: presentation.started, presentation.segment, presentation.ended
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import List, Optional

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False


@dataclass
class Segment:
    """One segment of a presentation."""
    title: str
    body: str
    gesture: str  # Camera movement to perform during/after this segment
    pause: float = 1.5  # Seconds to pause after speaking this segment


STAGE_GESTURES = {
    "scan_left", "scan_right", "scan_center", "nod_emphasis",
    "look_audience", "dramatic_pause", "sweep",
}

# Cycle through these gestures for each segment
GESTURE_CYCLE = [
    "scan_left", "nod_emphasis", "scan_right", "look_audience",
    "dramatic_pause", "scan_center", "nod_emphasis", "sweep",
]


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._presenting = False
        self._segments: List[Segment] = []
        self._current_segment = 0
        self._start_time: float = 0
        self._original_volume: str = "+0%"
        self._original_rate: str = "+0%"
        self._client = None
        self._model = "claude-sonnet-5-5"

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="presentation",
            version="1.0.0",
            description="Deliver timed presentations with camera choreography",
            requires={"tts"},
            optional={"servos"},
            pipeline_hooks=[PipelineHook.VOICE_PRE_AI],
            commands=[
                VoiceCommand(
                    triggers=["give a presentation", "present about", "do a presentation"],
                    handler="cmd_present",
                    description="Generate and deliver a presentation on a topic",
                ),
                VoiceCommand(
                    triggers=["present for"],
                    handler="cmd_present_timed",
                    description="Timed presentation: 'present for 3 minutes about AI'",
                ),
                VoiceCommand(
                    triggers=["stop presenting", "end presentation", "stop the presentation"],
                    handler="cmd_stop",
                ),
                VoiceCommand(
                    triggers=["pause presentation"],
                    handler="cmd_pause",
                ),
                VoiceCommand(
                    triggers=["resume presentation", "continue presenting"],
                    handler="cmd_resume",
                ),
                VoiceCommand(
                    triggers=["presentation status", "how long left"],
                    handler="cmd_status",
                ),
            ],
            emits_events=["presentation.started", "presentation.segment", "presentation.ended"],
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

    async def on_voice_pre_ai(self, text: str, user_name: str) -> Optional[str]:
        """While presenting, intercept voice to handle stop/pause without AI."""
        if not self._presenting:
            return None
        text_lower = text.lower()
        if "stop" in text_lower or "end" in text_lower:
            return await self.cmd_stop(text, user_name)
        if "pause" in text_lower:
            return await self.cmd_pause(text, user_name)
        # During presentation, ignore other speech
        return "I'm in the middle of a presentation. Say 'stop presenting' to end."

    def _extract_topic(self, text: str) -> str:
        """Extract the presentation topic from the voice command."""
        text_lower = text.lower()
        for prefix in ["present about ", "presentation about ", "presentation on ",
                        "give a presentation about ", "give a presentation on ",
                        "do a presentation about ", "do a presentation on "]:
            idx = text_lower.find(prefix)
            if idx >= 0:
                return text[idx + len(prefix):].strip().rstrip(".!?,")
        # Fallback: everything after "about"
        idx = text_lower.find("about ")
        if idx >= 0:
            return text[idx + 6:].strip().rstrip(".!?,")
        return text.strip().rstrip(".!?,")

    def _extract_duration(self, text: str) -> int:
        """Extract duration in minutes from text. Default 2 minutes."""
        match = re.search(r"(\d+)\s*minute", text.lower())
        if match:
            return min(15, max(1, int(match.group(1))))  # Clamp 1-15
        return 2

    async def _generate_presentation(self, topic: str, duration_minutes: int) -> List[Segment]:
        """Use Claude to generate a structured presentation."""
        if not self._client:
            # Fallback: simple single-segment presentation
            return [
                Segment(title="Introduction", body=f"Today I'd like to talk about {topic}.", gesture="scan_center"),
                Segment(title="Main Point", body=f"{topic} is a fascinating subject with many dimensions to explore.", gesture="look_audience"),
                Segment(title="Conclusion", body=f"Thank you for listening to my thoughts on {topic}.", gesture="nod_emphasis"),
            ]

        num_segments = max(3, duration_minutes * 2)  # ~2 segments per minute
        words_per_segment = int((duration_minutes * 150) / num_segments)  # ~150 words/min TTS

        prompt = (
            f"Write a {duration_minutes}-minute spoken presentation about: {topic}\n\n"
            f"Structure it as exactly {num_segments} segments. For each segment, provide:\n"
            f"- A short title (3-5 words)\n"
            f"- The spoken text (~{words_per_segment} words, conversational tone, meant to be "
            f"read aloud — no bullet points, no markdown, no stage directions)\n\n"
            f"Format each segment exactly like this:\n"
            f"[TITLE] Introduction to the Topic\n"
            f"[BODY] Welcome everyone. Today I want to talk about...\n\n"
            f"Make it engaging, clear, and suitable for a general audience. "
            f"Include an introduction and conclusion."
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=2000,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text
            return self._parse_segments(raw)
        except Exception as e:
            self.ctx.logger.error("Failed to generate presentation: %s", e)
            return [
                Segment(title="Presentation", body=f"I wanted to present about {topic}, but I'm having trouble generating the content right now.", gesture="scan_center"),
            ]

    def _parse_segments(self, raw_text: str) -> List[Segment]:
        """Parse Claude's response into Segment objects."""
        segments = []
        current_title = ""
        current_body = ""

        for line in raw_text.split("\n"):
            line = line.strip()
            if line.startswith("[TITLE]"):
                if current_title and current_body:
                    gesture = GESTURE_CYCLE[len(segments) % len(GESTURE_CYCLE)]
                    segments.append(Segment(title=current_title, body=current_body.strip(), gesture=gesture))
                current_title = line[7:].strip()
                current_body = ""
            elif line.startswith("[BODY]"):
                current_body = line[6:].strip() + " "
            elif current_title and not line.startswith("["):
                current_body += line + " "

        if current_title and current_body:
            gesture = GESTURE_CYCLE[len(segments) % len(GESTURE_CYCLE)]
            segments.append(Segment(title=current_title, body=current_body.strip(), gesture=gesture))

        if not segments:
            segments.append(Segment(title="Presentation", body=raw_text[:500], gesture="scan_center"))

        return segments

    async def _perform_gesture(self, gesture_name: str) -> None:
        """Execute a stage movement if servos are available."""
        if gesture_name not in STAGE_GESTURES or not self.ctx.expression.available:
            return
        if gesture_name == "scan_center":
            await self.ctx.expression.home()
        else:
            await self.ctx.expression.perform_gesture(gesture_name)

    async def _boost_volume(self) -> None:
        """Increase TTS volume for presentation projection."""
        self._original_volume = self.ctx.tts._config.volume
        self._original_rate = self.ctx.tts._config.rate
        self.ctx.tts._config.volume = "+30%"
        self.ctx.tts._config.rate = "-5%"  # Slightly slower for clarity

    async def _restore_volume(self) -> None:
        """Restore normal TTS volume."""
        self.ctx.tts._config.volume = self._original_volume
        self.ctx.tts._config.rate = self._original_rate

    async def _deliver_presentation(self) -> None:
        """Background task: deliver all segments with choreography."""
        self._start_time = time.monotonic()

        await self.ctx.event_bus.emit(
            "presentation.started", source=self.name,
            topic=self._segments[0].title if self._segments else "",
            num_segments=len(self._segments),
        )

        # Opening gesture: center and look at audience
        await self._perform_gesture("scan_center")
        await asyncio.sleep(0.5)

        for i, segment in enumerate(self._segments):
            if not self._presenting:
                break

            self._current_segment = i
            self.ctx.logger.info("Presenting segment %d/%d: %s", i + 1, len(self._segments), segment.title)

            await self.ctx.event_bus.emit(
                "presentation.segment", source=self.name,
                segment=i + 1, total=len(self._segments), title=segment.title,
            )

            # Speak the segment
            await self.ctx.tts.speak_and_wait(segment.body)

            if not self._presenting:
                break

            # Perform the choreographed gesture between segments
            await self._perform_gesture(segment.gesture)

            # Pause between segments
            await asyncio.sleep(segment.pause)

        # Wrap up
        self._presenting = False
        await self._restore_volume()
        if self.ctx.expression.available:
            await self.ctx.expression.home()

        elapsed = time.monotonic() - self._start_time
        await self.ctx.event_bus.emit(
            "presentation.ended", source=self.name,
            elapsed_seconds=elapsed,
        )
        self.ctx.logger.info("Presentation finished (%.0f seconds)", elapsed)

    async def cmd_present(self, text: str, user_name: str) -> Optional[str]:
        if self._presenting:
            return "I'm already presenting. Say 'stop presenting' first."

        topic = self._extract_topic(text)
        if not topic or len(topic) < 3:
            return "What should I present about? Say something like 'give a presentation about artificial intelligence'."

        duration = 2  # Default 2 minutes
        self._presenting = True

        await self._boost_volume()
        await self.ctx.tts.speak_and_wait(f"Preparing a presentation on {topic}. One moment.")

        self._segments = await self._generate_presentation(topic, duration)

        if not self._segments:
            self._presenting = False
            await self._restore_volume()
            return "I couldn't put together a presentation on that topic."

        # Deliver in background so voice pipeline isn't blocked
        self._spawn_task(self._deliver_presentation())
        return None  # Presentation starts speaking on its own

    async def cmd_present_timed(self, text: str, user_name: str) -> Optional[str]:
        if self._presenting:
            return "I'm already presenting. Say 'stop presenting' first."

        duration = self._extract_duration(text)
        topic = self._extract_topic(text)
        if not topic or len(topic) < 3:
            return "What topic? Say 'present for 3 minutes about space exploration'."

        self._presenting = True

        await self._boost_volume()
        await self.ctx.tts.speak_and_wait(
            f"Preparing a {duration} minute presentation on {topic}. Give me a moment."
        )

        self._segments = await self._generate_presentation(topic, duration)

        if not self._segments:
            self._presenting = False
            await self._restore_volume()
            return "I couldn't put that presentation together."

        self._spawn_task(self._deliver_presentation())
        return None

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        if not self._presenting:
            return "I'm not presenting right now."
        self._presenting = False
        self.ctx.tts.interrupt()
        await self._restore_volume()
        elapsed = time.monotonic() - self._start_time
        return f"Presentation ended. I spoke for about {int(elapsed)} seconds."

    async def cmd_pause(self, text: str, user_name: str) -> Optional[str]:
        if not self._presenting:
            return "I'm not presenting right now."
        self._presenting = False
        self.ctx.tts.interrupt()
        return f"Presentation paused at segment {self._current_segment + 1} of {len(self._segments)}. Say 'resume presentation' to continue."

    async def cmd_resume(self, text: str, user_name: str) -> Optional[str]:
        if self._presenting:
            return "I'm already presenting."
        if not self._segments or self._current_segment >= len(self._segments):
            return "No presentation to resume."

        self._presenting = True
        remaining = self._segments[self._current_segment:]
        self._segments = remaining
        self._spawn_task(self._deliver_presentation())
        return None

    async def cmd_status(self, text: str, user_name: str) -> Optional[str]:
        if not self._presenting:
            return "I'm not presenting right now."
        elapsed = int(time.monotonic() - self._start_time)
        seg = self._current_segment + 1
        total = len(self._segments)
        return f"Presenting segment {seg} of {total}. {elapsed} seconds elapsed."

    async def stop(self):
        self._presenting = False
        if self.ctx and self.ctx.tts:
            await self._restore_volume()
        await super().stop()
