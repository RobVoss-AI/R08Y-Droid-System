"""Voice command registry — routes transcribed speech to skill handlers.

Replaces the hardcoded SPECIAL_COMMANDS dict in ai_brain.py. Skills register
their own voice commands, and the registry finds the best match.
"""

import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RegisteredCommand:
    """A registered voice command with its handler."""
    triggers: List[str]
    skill_name: str
    handler: Callable  # async (text: str, user_name: str) -> Optional[str]
    description: str = ""
    priority: int = 0
    needs_follow_up: bool = False


class CommandRegistry:
    """Central registry for skill voice commands.

    Commands are matched by substring against transcribed text (case-insensitive).
    If multiple match, highest priority wins.
    """

    def __init__(self):
        self._commands: List[RegisteredCommand] = []

    def register(
        self,
        triggers: List[str],
        handler: Callable,
        skill_name: str,
        description: str = "",
        priority: int = 0,
        needs_follow_up: bool = False,
    ) -> None:
        cmd = RegisteredCommand(
            triggers=[t.lower() for t in triggers],
            skill_name=skill_name,
            handler=handler,
            description=description,
            priority=priority,
            needs_follow_up=needs_follow_up,
        )
        self._commands.append(cmd)
        self._commands.sort(key=lambda c: c.priority, reverse=True)
        logger.debug("Registered command: %s (skill: %s)", triggers, skill_name)

    def match(self, text: str) -> Optional[Tuple[RegisteredCommand, str]]:
        """Find the best matching command for transcribed text.

        Returns (command, matched_trigger) or None.
        """
        text_lower = text.lower().strip().rstrip("?!.")
        for cmd in self._commands:
            for trigger in cmd.triggers:
                if trigger in text_lower:
                    return cmd, trigger
        return None

    def list_commands(self) -> List[RegisteredCommand]:
        """List all registered commands (for help/discovery)."""
        return list(self._commands)
