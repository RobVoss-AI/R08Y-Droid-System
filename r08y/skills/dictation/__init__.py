"""Dictation skill — speak and R08Y writes to a text file.

When active, all speech is captured to a file instead of going to the AI.
Uses the VOICE_PRE_AI hook to intercept transcribed text.
"""

import time
from pathlib import Path
from typing import List, Optional

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._dictating = False
        self._buffer: List[str] = []
        self._file_path: Optional[str] = None

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="dictation",
            version="1.0.0",
            description="Dictation mode — speak and save to text file",
            requires={"tts"},
            pipeline_hooks=[PipelineHook.VOICE_PRE_AI],
            commands=[
                VoiceCommand(triggers=["start dictating", "dictation mode", "start dictation"], handler="cmd_start"),
                VoiceCommand(triggers=["stop dictating", "end dictation", "stop dictation"], handler="cmd_stop"),
                VoiceCommand(triggers=["new paragraph"], handler="cmd_paragraph"),
                VoiceCommand(triggers=["read it back", "read back"], handler="cmd_readback"),
                VoiceCommand(triggers=["save dictation", "save file"], handler="cmd_save"),
            ],
        )

    async def on_voice_pre_ai(self, text: str, user_name: str) -> Optional[str]:
        """When dictating, capture speech instead of sending to AI."""
        if not self._dictating:
            return None

        # Check for inline stop command
        text_lower = text.lower().strip()
        if "stop dictating" in text_lower or "end dictation" in text_lower:
            return await self.cmd_stop(text, user_name)

        if "new paragraph" in text_lower:
            self._buffer.append("\n\n")
            return "New paragraph."

        self._buffer.append(text + " ")
        word_count = sum(len(line.split()) for line in self._buffer)
        return f"Got it. {word_count} words so far."

    async def cmd_start(self, text: str, user_name: str) -> Optional[str]:
        if self._dictating:
            return "I'm already in dictation mode."
        self._dictating = True
        self._buffer = []
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self._file_path = str(Path(self.ctx.data_dir) / f"dictation_{timestamp}.txt")
        return "Dictation mode started. Everything you say will be written down. Say 'stop dictating' when done."

    async def cmd_stop(self, text: str, user_name: str) -> Optional[str]:
        if not self._dictating:
            return "I'm not in dictation mode."
        self._dictating = False
        content = "".join(self._buffer).strip()
        if content and self._file_path:
            Path(self._file_path).write_text(content)
            word_count = len(content.split())
            return f"Dictation saved. {word_count} words written to {Path(self._file_path).name}."
        return "Dictation ended. Nothing to save."

    async def cmd_paragraph(self, text: str, user_name: str) -> Optional[str]:
        if self._dictating:
            self._buffer.append("\n\n")
            return "New paragraph."
        return "I'm not in dictation mode."

    async def cmd_readback(self, text: str, user_name: str) -> Optional[str]:
        content = "".join(self._buffer).strip()
        if not content:
            return "Nothing to read back yet."
        # Truncate for TTS (don't read a novel)
        if len(content) > 500:
            return content[:500] + "... and more."
        return content

    async def cmd_save(self, text: str, user_name: str) -> Optional[str]:
        content = "".join(self._buffer).strip()
        if not content:
            return "Nothing to save."
        if self._file_path:
            Path(self._file_path).write_text(content)
            return f"Saved to {Path(self._file_path).name}."
        return "No file path set."
