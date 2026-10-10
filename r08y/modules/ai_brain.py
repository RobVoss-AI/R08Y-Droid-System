"""AI brain module for R08Y.

Sends transcribed user speech (with visual context) to Claude API and
returns conversational responses. Maintains per-user conversation history
in SQLite.
"""

import asyncio
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import aiosqlite

from r08y.config import AIConfig

logger = logging.getLogger(__name__)

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

    def _build_system_prompt(self, context_note: str) -> str:
        """Combine role, spoken-delivery guidance, and current visual context."""
        return (
            f"{self._config.system_prompt}\n\n"
            f"[Spoken response style: {self._config.spoken_style}]\n"
            f"[Visual context: {context_note}]"
        )

    async def think(self, user_text: str, visual_context: VisualContext, user_name: str = "unknown") -> str:
        """Process user speech and generate a response.

        Args:
            user_text: Transcribed speech from the user.
            visual_context: What R08Y currently sees.
            user_name: Recognized face name, or "unknown".

        Returns:
            Response text for TTS.
        """
        if not self.available:
            return "I'm having trouble thinking right now. My brain module isn't connected."

        # Build visual context prefix
        context_parts = []
        if visual_context.faces_visible > 0:
            if visual_context.is_known and visual_context.recognized_name:
                context_parts.append(f"You are currently looking at {visual_context.recognized_name}.")
            else:
                context_parts.append(f"You see {visual_context.faces_visible} face(s) but don't recognize them.")
        else:
            context_parts.append("You don't see anyone right now.")

        context_note = " ".join(context_parts)

        # Get or create conversation history for this user
        if user_name not in self._histories:
            self._histories[user_name] = await self._load_history(user_name)

        history = self._histories[user_name]

        # Add user turn
        history.append(ConversationTurn(role="user", content=user_text))

        # Trim history to max turns
        max_turns = self._config.max_history_turns
        if len(history) > max_turns:
            history = history[-max_turns:]
            self._histories[user_name] = history

        # Build messages for Claude
        system_prompt = self._build_system_prompt(context_note)
        messages = [{"role": t.role, "content": t.content} for t in history]

        try:
            response = await self._client.messages.create(
                model=self._config.model,
                max_tokens=300,
                system=system_prompt,
                messages=messages,
            )
            reply = response.content[0].text

            # Add assistant turn
            history.append(ConversationTurn(role="assistant", content=reply))

            # Persist to database
            await self._save_turn(user_name, "user", user_text)
            await self._save_turn(user_name, "assistant", reply)

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
