"""R08Y Skill System — auto-discovery, lifecycle management, pipeline integration.

Skills are discovered by scanning r08y/skills/*/  for directories containing
a `Skill` class that inherits from BaseSkill. Each skill is loaded, checked
against available hardware, wired into pipelines, and started.

Adding a new skill: create r08y/skills/my_skill/__init__.py with a `Skill` class.
No changes to main.py or ai_brain.py needed.
"""

import asyncio
import importlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import numpy as np

from r08y.skills._base import BaseSkill, PipelineHook, SkillContext
from r08y.skills._bus import EventBus
from r08y.skills._registry import CommandRegistry

logger = logging.getLogger(__name__)

# Re-export for convenience
__all__ = ["BaseSkill", "PipelineHook", "SkillContext", "EventBus", "CommandRegistry", "SkillManager"]


class SkillManager:
    """Discovers, loads, and manages the lifecycle of all skills."""

    def __init__(self, config: Dict[str, Any]):
        self._config = config  # Full config dict (skills section)
        self.event_bus = EventBus()
        self.command_registry = CommandRegistry()
        self._skills: Dict[str, BaseSkill] = {}
        self._vision_frame_skills: List[BaseSkill] = []
        self._vision_detection_skills: List[BaseSkill] = []
        self._voice_pre_ai_skills: List[BaseSkill] = []
        self._voice_post_ai_skills: List[BaseSkill] = []
        self._frame_timestamps: Dict[str, float] = {}

    def discover_skills(self) -> List[str]:
        """Scan the skills directory and find all valid skill modules."""
        skills_dir = Path(__file__).parent
        found = []
        for entry in sorted(skills_dir.iterdir()):
            if entry.is_dir() and not entry.name.startswith("_") and (entry / "__init__.py").exists():
                found.append(entry.name)
        logger.info("Discovered %d skills: %s", len(found), ", ".join(found))
        return found

    def load_skills(self, available_hardware: Set[str]) -> None:
        """Import and instantiate all discovered skills that meet requirements."""
        skill_names = self.discover_skills()
        skills_config = self._config.get("skills", {})
        disabled = set(skills_config.get("disabled", []))

        for name in skill_names:
            if name in disabled:
                logger.info("Skill '%s' disabled in config — skipping", name)
                continue

            try:
                module = importlib.import_module(f"r08y.skills.{name}")
                skill_class = getattr(module, "Skill", None)
                if skill_class is None or not issubclass(skill_class, BaseSkill):
                    logger.warning("Skill '%s' has no valid Skill class — skipping", name)
                    continue

                skill = skill_class()
                manifest = skill.manifest

                # Check required hardware
                missing = manifest.requires - available_hardware
                if missing:
                    logger.warning("Skill '%s' requires %s (not available) — skipping", name, missing)
                    continue

                self._skills[name] = skill
                logger.info("Loaded skill: %s v%s — %s", manifest.name, manifest.version, manifest.description)

            except Exception:
                logger.exception("Failed to load skill '%s'", name)

    async def setup_all(
        self, servo, tts, camera, state, mobility=None, distance=None, light_sensors=None
    ) -> None:
        """Inject runtime context into all loaded skills and register commands."""
        failed = []
        for name, skill in list(self._skills.items()):
            try:
                skill_config = self._config.get("skills", {}).get(name, {})
                data_dir = str(Path("data/skills") / name)
                os.makedirs(data_dir, exist_ok=True)

                context = SkillContext(
                    event_bus=self.event_bus,
                    command_registry=self.command_registry,
                    servo=servo,
                    tts=tts,
                    camera=camera,
                    mobility=mobility,
                    distance=distance,
                    light_sensors=light_sensors,
                    state=state,
                    config=skill_config,
                    data_dir=data_dir,
                    logger=logging.getLogger(f"r08y.skills.{name}"),
                )
                await skill.setup(context)

                # Register voice commands
                for cmd in skill.manifest.commands:
                    handler = getattr(skill, cmd.handler, None)
                    if handler and callable(handler):
                        self.command_registry.register(
                            triggers=cmd.triggers,
                            handler=handler,
                            skill_name=name,
                            description=cmd.description,
                            needs_follow_up=cmd.needs_follow_up,
                        )

                # Categorize pipeline hooks
                hooks = skill.manifest.pipeline_hooks
                if PipelineHook.VISION_FRAME in hooks:
                    self._vision_frame_skills.append(skill)
                if PipelineHook.VISION_DETECTIONS in hooks:
                    self._vision_detection_skills.append(skill)
                if PipelineHook.VOICE_PRE_AI in hooks:
                    self._voice_pre_ai_skills.append(skill)
                if PipelineHook.VOICE_POST_AI in hooks:
                    self._voice_post_ai_skills.append(skill)

            except Exception:
                logger.exception("Failed to setup skill '%s'", name)
                self.command_registry.unregister_skill(name)
                failed.append(name)

        for name in failed:
            self._skills.pop(name, None)

    async def start_all(self) -> None:
        """Start all loaded skills."""
        failed = []
        for name, skill in list(self._skills.items()):
            try:
                await skill.start()
                logger.info("Started skill: %s", name)
            except Exception:
                logger.exception("Failed to start skill '%s'", name)
                self.command_registry.unregister_skill(name)
                failed.append(name)

        for name in failed:
            skill = self._skills.pop(name, None)
            for collection in (
                self._vision_frame_skills,
                self._vision_detection_skills,
                self._voice_pre_ai_skills,
                self._voice_post_ai_skills,
            ):
                if skill in collection:
                    collection.remove(skill)

    async def stop_all(self) -> None:
        """Stop all loaded skills."""
        for name, skill in self._skills.items():
            try:
                await skill.stop()
            except Exception:
                logger.exception("Error stopping skill '%s'", name)

    # --- Pipeline integration methods (called from main.py pipelines) ---

    async def on_vision_frame(self, frame: np.ndarray) -> None:
        """Dispatch frame to skills with VISION_FRAME hook (respecting throttle)."""
        now = time.monotonic()
        for skill in self._vision_frame_skills:
            interval = skill.manifest.vision_frame_interval
            last = self._frame_timestamps.get(skill.name, 0)
            if now - last >= interval:
                self._frame_timestamps[skill.name] = now
                try:
                    await skill.on_vision_frame(frame)
                except Exception:
                    logger.exception("Skill '%s' vision_frame error", skill.name)

    async def on_vision_detections(self, frame: np.ndarray, detections: list) -> None:
        """Dispatch face detections to skills with VISION_DETECTIONS hook."""
        for skill in self._vision_detection_skills:
            try:
                await skill.on_vision_detections(frame, detections)
            except Exception:
                logger.exception("Skill '%s' vision_detections error", skill.name)

    async def on_voice_pre_ai(self, text: str, user_name: str) -> Optional[str]:
        """Let skills intercept speech before AI. First non-None response wins."""
        for skill in self._voice_pre_ai_skills:
            try:
                result = await skill.on_voice_pre_ai(text, user_name)
                if result is not None:
                    logger.debug("Skill '%s' intercepted voice: '%s'", skill.name, result[:50])
                    return result
            except Exception:
                logger.exception("Skill '%s' voice_pre_ai error", skill.name)
        return None

    async def on_voice_post_ai(self, response: str) -> str:
        """Let skills transform AI response before TTS."""
        for skill in self._voice_post_ai_skills:
            try:
                response = await skill.on_voice_post_ai(response)
            except Exception:
                logger.exception("Skill '%s' voice_post_ai error", skill.name)
        return response

    def match_command(self, text: str):
        """Check if text matches any registered skill command."""
        return self.command_registry.match(text)

    @property
    def loaded_skills(self) -> Dict[str, BaseSkill]:
        return dict(self._skills)
