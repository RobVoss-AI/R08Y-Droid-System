"""Base class and context types for R08Y skills.

Every skill inherits from BaseSkill and declares a SkillManifest describing
what it needs, what commands it handles, and what events it emits/listens to.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Set, TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from r08y.skills._bus import EventBus
    from r08y.skills._registry import CommandRegistry
    from r08y.modules.servo_controller import MotorController
    from r08y.modules.text_to_speech import TextToSpeech
    from r08y.modules.camera import CameraCapture
    from r08y.modules.mobility import MobilityController
    from r08y.modules.sensors import DistanceSensor, LightSensors
    from r08y.main import DroidState


class PipelineHook(Enum):
    """Which pipeline hooks a skill can subscribe to."""
    VISION_FRAME = auto()
    VISION_DETECTIONS = auto()
    VOICE_PRE_AI = auto()
    VOICE_POST_AI = auto()


@dataclass
class VoiceCommand:
    """A voice command that a skill responds to."""
    triggers: List[str]
    handler: str  # Method name on the skill to call
    description: str = ""
    needs_follow_up: bool = False


@dataclass
class SkillManifest:
    """Declarative description of what a skill needs and provides."""
    name: str
    version: str = "1.0.0"
    description: str = ""
    pipeline_hooks: List[PipelineHook] = field(default_factory=list)
    commands: List[VoiceCommand] = field(default_factory=list)
    emits_events: List[str] = field(default_factory=list)
    listens_events: List[str] = field(default_factory=list)
    requires: Set[str] = field(default_factory=set)
    optional: Set[str] = field(default_factory=set)
    vision_frame_interval: float = 0.1


@dataclass
class SkillContext:
    """Runtime context injected into skills — their interface to the droid."""
    event_bus: "EventBus"
    command_registry: "CommandRegistry"
    servo: "MotorController"
    tts: "TextToSpeech"
    camera: "CameraCapture"
    mobility: "MobilityController"
    distance: "DistanceSensor"
    light_sensors: "LightSensors"
    state: "DroidState"
    config: Dict[str, Any]
    data_dir: str
    logger: logging.Logger


class BaseSkill(ABC):
    """Abstract base class for all R08Y skills.

    Lifecycle:
      1. __init__() — no I/O
      2. setup(context) — receive runtime context, register event handlers
      3. start() — activate (start background tasks, open hardware)
      4. stop() — deactivate (cancel tasks, release resources)
    """

    def __init__(self):
        self._context: Optional[SkillContext] = None
        self._running: bool = False
        self._tasks: List[asyncio.Task] = []

    @property
    @abstractmethod
    def manifest(self) -> SkillManifest:
        ...

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def ctx(self) -> SkillContext:
        assert self._context is not None, f"Skill {self.name} accessed context before setup()"
        return self._context

    async def setup(self, context: SkillContext) -> None:
        """Receive runtime context. Override to add custom setup."""
        self._context = context
        # Auto-register event listeners from manifest
        for event_name in self.manifest.listens_events:
            handler = getattr(self, f"on_{event_name.replace('.', '_')}", None)
            if handler and callable(handler):
                context.event_bus.subscribe(event_name, handler)

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    # --- Pipeline hook methods (override as needed) ---

    async def on_vision_frame(self, frame: np.ndarray) -> None:
        pass

    async def on_vision_detections(self, frame: np.ndarray, detections: list) -> None:
        pass

    async def on_voice_pre_ai(self, text: str, user_name: str) -> Optional[str]:
        """Return a response to short-circuit AI, or None to pass through."""
        return None

    async def on_voice_post_ai(self, response: str) -> str:
        """Transform AI response before TTS. Return modified text."""
        return response

    # --- Utility helpers ---

    def _spawn_task(self, coro) -> asyncio.Task:
        """Spawn a tracked background task (auto-cancelled on stop)."""
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task
