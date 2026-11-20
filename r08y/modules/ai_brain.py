"""AI brain module for R08Y.

Sends transcribed user speech (with visual context) to Claude API and
returns conversational responses. Claude can act through RobotTools (gestures,
camera, head direction, sensors, guarded driving) before it answers, the way
Reachy Mini's conversation app lets its model move the robot. Maintains
per-user conversation history in SQLite.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable, Dict, List, Optional

import aiosqlite

from r08y.config import AIConfig

logger = logging.getLogger(__name__)

# Models that accept the server-side refusal fallback ("default" routing).
FALLBACK_MODELS = {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1"}
FALLBACK_BETA = "server-side-fallback-2026-07-01"

REFUSAL_REPLY = "That's one I can't help with."

TOOL_GUIDANCE = (
    "[Body and actions: You have a pan/tilt camera head on a small wheeled body, "
    "and tools that move it. Act with them instead of only talking: use express "
    "when a reaction would feel natural, look_with_camera whenever someone asks "
    "what you see or seeing would help, and move_head or set_face_tracking when "
    "asked where to look. When an action takes a moment, put a few spoken words "
    "first in the same reply, such as \"Let me look.\" After acting, answer in a "
    "short spoken sentence, or say nothing if the gesture says enough. Call "
    "end_conversation when the person says goodbye or is finished.]"
)

# Anthropic SDK is required for this module
try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False
    logger.warning("anthropic SDK not installed — AI brain disabled")


@dataclass
class VisualContext:
    """What R08Y currently sees."""
    faces_visible: int = 0
    recognized_name: Optional[str] = None
    is_known: bool = False


@dataclass
class ConversationTurn:
    role: str  # "user" or "assistant"
    content: str


class AIBrain:
    """Conversational AI powered by Claude API with visual context awareness."""

    # Special commands that trigger droid actions instead of normal chat
    SPECIAL_COMMANDS = {
        "remember my face": "register_face",
        "learn my face": "register_face",
        "who am i": "identify",
        "look around": "patrol",
        "forget my face": "delete_face",
    }

    def __init__(self, config: AIConfig):
        self._config = config
        self._client = None
        self._db: Optional[aiosqlite.Connection] = None
        self._histories: Dict[str, List[ConversationTurn]] = {}
        self.available = False

    async def start(self) -> None:
        """Initialize the Claude client and conversation database."""
        if not ANTHROPIC_AVAILABLE:
            return
        if not self._config.api_key or self._config.api_key.startswith("YOUR_"):
            logger.warning("Anthropic API key not configured — AI brain disabled")
            return

        self._client = anthropic.AsyncAnthropic(api_key=self._config.api_key)

        # Initialize conversation database
        db_path = Path(self._config.conversations_db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_name TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL
            )
        """)
        await self._db.commit()

        self.available = True
        logger.info("AI brain initialized (model: %s)", self._config.model)

    def check_special_command(self, text: str) -> Optional[str]:
        """Check if user text matches a special droid command.

        Returns the command key (e.g., 'register_face') or None.
        """
        text_lower = text.lower().strip().rstrip("?!.")
        for trigger, command in self.SPECIAL_COMMANDS.items():
            if trigger in text_lower:
                return command
        return None

    def _stable_system_prompt(self, with_tools: bool = False) -> str:
        """Role and spoken-delivery guidance that stays fixed between turns."""
        prompt = (
            f"{self._config.system_prompt}\n\n"
            f"[Personality: {self._config.personality_profile}]\n"
            f"[Spoken response style: {self._config.spoken_style}]"
        )
        if with_tools:
            prompt += f"\n{TOOL_GUIDANCE}"
        return prompt

    def _build_system_prompt(self, context_note: str) -> str:
        """Combine role, spoken-delivery guidance, and current visual context."""
        return f"{self._stable_system_prompt()}\n[Visual context: {context_note}]"

    @staticmethod
    def _context_note(visual_context: VisualContext, now: Optional[datetime] = None) -> str:
        context_parts = []
        if visual_context.faces_visible > 0:
            if visual_context.is_known and visual_context.recognized_name:
                context_parts.append(f"You are currently looking at {visual_context.recognized_name}.")
            else:
                context_parts.append(f"You see {visual_context.faces_visible} face(s) but don't recognize them.")
        else:
            context_parts.append("You don't see anyone right now.")
        now = now or datetime.now().astimezone()
        context_parts.append(f"Local time: {now.strftime('%A %B %d, %Y, %I:%M %p')}.")
        return " ".join(context_parts)

    async def _create_message(self, system: list, messages: list, tools: list):
        request = dict(
            model=self._config.model,
            max_tokens=self._config.max_tokens,
            system=system,
            messages=messages,
            output_config={"effort": self._config.effort},
        )
        if tools:
            request["tools"] = tools
        if self._config.refusal_fallback and self._config.model in FALLBACK_MODELS:
            return await self._client.beta.messages.create(
                betas=[FALLBACK_BETA],
                fallbacks="default",
                **request,
            )
        return await self._client.messages.create(**request)

    async def think(
        self,
        user_text: str,
        visual_context: VisualContext,
        user_name: str = "unknown",
        *,
        tools=None,
        on_interim_text: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> str:
        """Process user speech and generate a response.

        Args:
            user_text: Transcribed speech from the user.
            visual_context: What R08Y currently sees.
            user_name: Recognized face name, or "unknown".
            tools: Optional RobotTools; Claude may call them before replying.
            on_interim_text: Receives words Claude says before an action
                ("Let me look.") so they can play while the action runs.

        Returns:
            Response text for TTS. Empty when Claude answered with a gesture only.
        """
        if not self.available:
            return "I'm having trouble thinking right now. My brain module isn't connected."

        context_note = self._context_note(visual_context)

        # Get or create conversation history for this user
        if user_name not in self._histories:
            self._histories[user_name] = await self._load_history(user_name)

        history = self._histories[user_name]

        # Add user turn
        history.append(ConversationTurn(role="user", content=user_text))

        # Trim history to max turns. The API requires the first message to come
        # from the user, so drop any assistant turn left at the front.
        max_turns = self._config.max_history_turns
        if len(history) > max_turns:
            history = history[-max_turns:]
        while history and history[0].role != "user":
            history = history[1:]
        self._histories[user_name] = history

        tool_definitions = []
        if tools is not None and self._config.tools_enabled:
            tools.begin_turn()
            tool_definitions = tools.definitions()

        # The fixed prompt is cached; the per-turn context follows it. Earlier
        # turns are sent as plain text, and this turn's tool rounds are
        # appended unchanged, so the history stays append-only.
        system = [
            {
                "type": "text",
                "text": self._stable_system_prompt(with_tools=bool(tool_definitions)),
                "cache_control": {"type": "ephemeral"},
            },
            {"type": "text", "text": f"[Visual context: {context_note}]"},
        ]
        messages = [{"role": t.role, "content": t.content} for t in history]

        try:
            spoken: List[str] = []
            reply = ""
            for round_index in range(self._config.max_tool_rounds + 1):
                response = await self._create_message(system, messages, tool_definitions)
                if response.stop_reason == "refusal":
                    details = getattr(response, "stop_details", None)
                    logger.warning(
                        "Claude declined the request (category: %s)",
                        getattr(details, "category", None),
                    )
                    reply = REFUSAL_REPLY
                    break

                text = " ".join(
                    block.text.strip()
                    for block in response.content
                    if block.type == "text" and block.text.strip()
                )
                tool_uses = [block for block in response.content if block.type == "tool_use"]
                if (
                    response.stop_reason != "tool_use"
                    or not tool_uses
                    or round_index == self._config.max_tool_rounds
                ):
                    reply = text
                    break

                if text:
                    spoken.append(text)
                    if on_interim_text is not None:
                        await on_interim_text(text)

                messages.append({"role": "assistant", "content": response.content})
                results = []
                # Run in order: "look left, then tell me what you see" needs the
                # head to move before the photo.
                for block in tool_uses:
                    logger.info("Claude tool: %s %s", block.name, block.input)
                    outcome = await tools.execute(block.name, block.input, user_name=user_name)
                    result = {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": outcome.content,
                    }
                    if outcome.is_error:
                        result["is_error"] = True
                    results.append(result)
                messages.append({"role": "user", "content": results})

            remembered = " ".join([*spoken, reply]).strip()
            if remembered:
                history.append(ConversationTurn(role="assistant", content=remembered))

            # Persist to database
            await self._save_turn(user_name, "user", user_text)
            if remembered:
                await self._save_turn(user_name, "assistant", remembered)

            logger.info("AI response for %s: '%s'", user_name, reply[:80])
            return reply

        except anthropic.APIConnectionError:
            logger.error("Cannot reach Claude API — no network?")
            return "I can't reach my brain right now. Check the network connection."
        except anthropic.RateLimitError:
            logger.warning("Claude API rate limit hit")
            return "I need a moment to think. I'm being rate limited."
        except Exception:
            logger.exception("Claude API error")
            return "Something went wrong with my thinking. Try again in a moment."

    async def _save_turn(self, user_name: str, role: str, content: str) -> None:
        """Save a conversation turn to the database."""
        if self._db:
            await self._db.execute(
                "INSERT INTO conversations (user_name, role, content, timestamp) VALUES (?, ?, ?, ?)",
                (user_name, role, content, time.time()),
            )
            await self._db.commit()

    async def _load_history(self, user_name: str) -> List[ConversationTurn]:
        """Load recent conversation history for a user from the database."""
        if not self._db:
            return []
        history = []
        async with self._db.execute(
            "SELECT role, content FROM conversations WHERE user_name = ? ORDER BY timestamp DESC LIMIT ?",
            (user_name, self._config.max_history_turns),
        ) as cursor:
            rows = await cursor.fetchall()
            for role, content in reversed(rows):
                history.append(ConversationTurn(role=role, content=content))
        return history

    async def stop(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
        self.available = False
