"""Physical actions Claude can choose during a conversation.

The tool set follows Pollen Robotics' Reachy Mini conversation app
(``move_head``, ``play_emotion``, ``camera``, ``head_tracking``) mapped onto
R08Y's pan/tilt head, plus guarded mecanum driving, sensor readings, face
introductions, and R08Y's existing voice skills.

Every action goes through the same safety layers as voice commands: the
expression controller clamps head poses, and mobility keeps its distance guard,
duration cap, and watchdog.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

from r08y.config import AIConfig, MobilityConfig
from r08y.modules.mobility import MobilityError, MobilitySafetyError

logger = logging.getLogger(__name__)

# Emotions Claude may show; each name is a gesture in expression_controller.
EMOTIONS = (
    "happy",
    "excited",
    "pleased",
    "proud",
    "curious",
    "attentive",
    "thinking",
    "surprised",
    "confused",
    "uncertain",
    "shy",
    "sad",
    "sleepy",
    "greet",
    "nod",
    "shake",
    "dance",
    "alert",
)

HEAD_DIRECTIONS = {
    "left": "look_left",
    "right": "look_right",
    "up": "look_up",
    "down": "look_down",
    "front": "center",
}

# (forward, strafe right, turn left) for mobility.move_for.
DRIVE_DIRECTIONS = {
    "forward": (1.0, 0.0, 0.0),
    "backward": (-1.0, 0.0, 0.0),
    "strafe_left": (0.0, -1.0, 0.0),
    "strafe_right": (0.0, 1.0, 0.0),
    "turn_left": (0.0, 0.0, 1.0),
    "turn_right": (0.0, 0.0, -1.0),
}

# How long a deliberate look holds before face tracking resumes.
LOOK_HOLD_SECONDS = 5.0
# Let the head settle after a look before taking a photo.
CAMERA_SETTLE_SECONDS = 0.5
MAX_PHOTO_WIDTH = 1024

ToolContent = Union[str, List[Dict[str, Any]]]


@dataclass
class ToolOutcome:
    content: ToolContent
    is_error: bool = False


class RobotTools:
    """Build Claude tool definitions from live hardware and run the calls."""

    def __init__(
        self,
        config: AIConfig,
        mobility_config: Optional[MobilityConfig] = None,
        *,
        expression=None,
        face_tracker=None,
        camera=None,
        mobility=None,
        distance=None,
        light_sensors=None,
        battery=None,
        face_recognizer=None,
        state=None,
        skill_manager=None,
    ):
        self._config = config
        self._mobility_config = mobility_config or MobilityConfig()
        self._expression = expression
        self._face_tracker = face_tracker
        self._camera = camera
        self._mobility = mobility
        self._distance = distance
        self._light_sensors = light_sensors
        self._battery = battery
        self._face_recognizer = face_recognizer
        self._state = state
        self._skill_manager = skill_manager
        self._drive_lock = asyncio.Lock()
        self._last_head_move = 0.0
        self.expressed = False
        self.conversation_ended = False
        # Built once so the tool list stays identical across requests, which
        # keeps the prompt cache warm.
        self._definitions = self._build_definitions()
        self._handlers = {
            definition["name"]: getattr(self, f"_tool_{definition['name']}")
            for definition in self._definitions
        }

    # ------------------------------------------------------------------ setup

    @staticmethod
    def _available(component) -> bool:
        return component is not None and bool(getattr(component, "available", False))

    @property
    def can_drive(self) -> bool:
        return self._config.allow_driving and self._available(self._mobility)

    def _skill_phrases(self) -> List[str]:
        if self._skill_manager is None:
            return []
        phrases = []
        for command in self._skill_manager.command_registry.list_commands():
            if command.skill_name == "mobility_control" and not self.can_drive:
                continue
            if command.triggers:
                phrases.append(command.triggers[0])
        return sorted(set(phrases))

    def _build_definitions(self) -> List[Dict[str, Any]]:
        tools: List[Dict[str, Any]] = []
        if self._available(self._expression):
            tools.append({
                "name": "express",
                "description": (
                    "Show an emotion with a short head gesture while you speak. Use it "
                    "when a reaction would feel natural, at most once per reply. Skip it "
                    "for neutral replies. The gesture replaces describing your feelings "
                    "in words."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"emotion": {"type": "string", "enum": list(EMOTIONS)}},
                    "required": ["emotion"],
                    "additionalProperties": False,
                },
            })
            tools.append({
                "name": "move_head",
                "description": (
                    "Point your camera head left, right, up, or down and hold that look "
                    "for a few seconds, or 'front' to look straight ahead. Use it when "
                    "someone asks you to look somewhere."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": list(HEAD_DIRECTIONS)}
                    },
                    "required": ["direction"],
                    "additionalProperties": False,
                },
            })
            if self._face_tracker is not None:
                tools.append({
                    "name": "set_face_tracking",
                    "description": (
                        "Turn face following on or off. It starts on. Turn it off when "
                        "someone asks you to stop following them or to keep looking "
                        "somewhere; turn it back on when they ask you to look at them."
                    ),
                    "input_schema": {
                        "type": "object",
                        "properties": {"enabled": {"type": "boolean"}},
                        "required": ["enabled"],
                        "additionalProperties": False,
                    },
                })
        if self._available(self._camera):
            tools.append({
                "name": "look_with_camera",
                "description": (
                    "Take a photo with your camera and look at it. Use it whenever "
                    "someone asks what you see, wants something identified or read, "
                    "asks how they look, or when seeing would help you answer. Describe "
                    "only what the photo shows; say so when it is unclear."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "question": {
                            "type": "string",
                            "description": "What to look for or answer from the photo.",
                        }
                    },
                    "required": ["question"],
                    "additionalProperties": False,
                },
            })
        if self.can_drive:
            tools.append({
                "name": "drive",
                "description": (
                    "Drive your mecanum wheels for a moment. Only drive when the person "
                    "asks you to move. Forward motion is blocked when your distance "
                    "sensor sees an obstacle, and every move is capped at "
                    f"{self._mobility_config.max_duration:.1f} seconds. If a move is "
                    "blocked, say why instead of retrying."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "direction": {"type": "string", "enum": list(DRIVE_DIRECTIONS)},
                        "seconds": {
                            "type": "number",
                            "description": (
                                "How long to move, 0.2 to "
                                f"{self._mobility_config.max_duration:.1f}."
                            ),
                        },
                    },
                    "required": ["direction", "seconds"],
                    "additionalProperties": False,
                },
            })
        if any(
            self._available(component)
            for component in (self._distance, self._light_sensors, self._battery)
        ):
            tools.append({
                "name": "read_sensors",
                "description": (
                    "Read your ultrasonic distance sensor, your two light sensors, and "
                    "your battery level. Use it when asked how far something is, how "
                    "bright it is, or how much charge you have."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            })
        if self._available(self._face_recognizer) and self._available(self._camera):
            tools.append({
                "name": "learn_face",
                "description": (
                    "Remember the face in front of your camera under a name so you "
                    "recognize that person later. Use it only after the person tells "
                    "you their name and asks or agrees to be remembered."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                },
            })
        phrases = self._skill_phrases()
        if phrases:
            tools.append({
                "name": "use_skill",
                "description": (
                    "Run one of your built-in skills by its trigger phrase, adding any "
                    "details the skill needs (for example 'set a timer for 5 minutes'). "
                    "Trigger phrases: " + "; ".join(phrases) + "."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {"phrase": {"type": "string"}},
                    "required": ["phrase"],
                    "additionalProperties": False,
                },
            })
        tools.append({
            "name": "end_conversation",
            "description": (
                "Stop listening for a follow-up and wait for your wake word again. Call "
                "it when the person says goodbye or clearly has nothing more to say."
            ),
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        })
        return tools

    # -------------------------------------------------------------- interface

    def definitions(self) -> List[Dict[str, Any]]:
        return self._definitions

    @property
    def names(self) -> List[str]:
        return [definition["name"] for definition in self._definitions]

    def begin_turn(self) -> None:
        """Reset per-turn flags before a new user utterance."""
        self.expressed = False
        self.conversation_ended = False

    async def execute(
        self, name: str, tool_input: Any, *, user_name: str = "unknown"
    ) -> ToolOutcome:
        handler = self._handlers.get(name)
        if handler is None:
            # Accept a near-miss in letter case rather than failing the turn.
            matches = [key for key in self._handlers if key.casefold() == name.casefold()]
            handler = self._handlers[matches[0]] if len(matches) == 1 else None
        if handler is None:
            return ToolOutcome(
                f"Unknown tool '{name}'. Available tools: {', '.join(self.names)}.",
                is_error=True,
            )
        if not isinstance(tool_input, dict):
            return ToolOutcome("Tool input must be a JSON object.", is_error=True)
        try:
            return await handler(tool_input, user_name)
        except Exception as exc:
            logger.exception("Tool '%s' failed", name)
            return ToolOutcome(f"{name} failed: {type(exc).__name__}", is_error=True)

    # ------------------------------------------------------------------ tools

    async def _tool_express(self, tool_input: dict, user_name: str) -> ToolOutcome:
        emotion = tool_input.get("emotion")
        if emotion not in EMOTIONS:
            return ToolOutcome(
                f"emotion must be one of: {', '.join(EMOTIONS)}.", is_error=True
            )
        accepted = await self._expression.perform_gesture(emotion, wait=False, priority=8)
        if not accepted:
            return ToolOutcome("Your head is busy; the gesture was skipped.")
        self.expressed = True
        return ToolOutcome(f"Showing {emotion}.")

    async def _tool_move_head(self, tool_input: dict, user_name: str) -> ToolOutcome:
        direction = tool_input.get("direction")
        gesture = HEAD_DIRECTIONS.get(direction)
        if gesture is None:
            return ToolOutcome(
                f"direction must be one of: {', '.join(HEAD_DIRECTIONS)}.", is_error=True
            )
        if self._face_tracker is not None:
            self._face_tracker.hold(LOOK_HOLD_SECONDS)
        await self._expression.perform_gesture(gesture, wait=True, priority=6)
        self._last_head_move = time.monotonic()
        self.expressed = True
        if direction == "front":
            return ToolOutcome("Looking straight ahead.")
        return ToolOutcome(f"Looking {direction}. Face tracking resumes in a few seconds.")

    async def _tool_set_face_tracking(self, tool_input: dict, user_name: str) -> ToolOutcome:
        enabled = tool_input.get("enabled")
        if not isinstance(enabled, bool):
            return ToolOutcome("enabled must be true or false.", is_error=True)
        self._face_tracker.set_enabled(enabled)
        return ToolOutcome("Face tracking on." if enabled else "Face tracking off.")

    async def _tool_look_with_camera(self, tool_input: dict, user_name: str) -> ToolOutcome:
        question = str(tool_input.get("question") or "Describe what you see.").strip()
        settle = CAMERA_SETTLE_SECONDS - (time.monotonic() - self._last_head_move)
        if settle > 0:
            await asyncio.sleep(settle)
        frame = await self._camera.get_frame()
        if frame is None:
            return ToolOutcome("The camera returned no image.", is_error=True)
        jpeg = await asyncio.to_thread(_encode_jpeg, frame)
        if jpeg is None:
            return ToolOutcome("The photo could not be encoded.", is_error=True)

        notes = [f"Photo taken just now through your camera. Question: {question}"]
        age = getattr(self._camera, "frame_age", 0.0)
        if age > 2.0:
            notes.append(f"Warning: this frame is {age:.0f} seconds old.")
        if self._state is not None:
            recognized = getattr(self._state, "recognized", None)
            if recognized is not None and getattr(recognized, "is_known", False):
                notes.append(f"Face recognition says {recognized.name} is in view.")
        return ToolOutcome([
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.standard_b64encode(jpeg).decode("ascii"),
                },
            },
            {"type": "text", "text": " ".join(notes)},
        ])

    async def _tool_drive(self, tool_input: dict, user_name: str) -> ToolOutcome:
        direction = tool_input.get("direction")
        vector = DRIVE_DIRECTIONS.get(direction)
        if vector is None:
            return ToolOutcome(
                f"direction must be one of: {', '.join(DRIVE_DIRECTIONS)}.", is_error=True
            )
        try:
            seconds = float(tool_input.get("seconds", self._mobility_config.default_duration))
        except (TypeError, ValueError):
            return ToolOutcome("seconds must be a number.", is_error=True)
        seconds = max(0.2, min(self._mobility_config.max_duration, seconds))
        speed = self._mobility_config.default_speed
        forward, strafe, turn = vector
        async with self._drive_lock:
            try:
                await self._mobility.move_for(
                    forward * speed, strafe * speed, turn * speed, seconds
                )
            except MobilitySafetyError as exc:
                await self._mobility.emergency_stop()
                return ToolOutcome(f"Move blocked for safety: {exc}", is_error=True)
            except MobilityError as exc:
                await self._mobility.emergency_stop()
                return ToolOutcome(f"Wheels unavailable: {exc}", is_error=True)
        return ToolOutcome(f"Moved {direction.replace('_', ' ')} for {seconds:.1f} seconds.")

    async def _tool_read_sensors(self, tool_input: dict, user_name: str) -> ToolOutcome:
        lines = []
        if self._available(self._distance):
            distance = await self._distance.read_cm()
            lines.append(
                f"Distance ahead: {distance:.0f} cm."
                if distance is not None
                else "Distance ahead: no valid reading (nothing in range or sensor error)."
            )
        if self._available(self._light_sensors):
            reading = await self._light_sensors.read()
            if reading is not None:
                lines.append(
                    f"Light sensors: left {reading.left_volts:.2f} V, "
                    f"right {reading.right_volts:.2f} V."
                )
            else:
                lines.append("Light sensors: no reading.")
        if self._available(self._battery):
            battery = await self._battery.read()
            lines.append(
                f"Battery: {battery.describe()}." if battery is not None else "Battery: no reading."
            )
        return ToolOutcome(" ".join(lines) or "No sensors are available.")

    async def _tool_learn_face(self, tool_input: dict, user_name: str) -> ToolOutcome:
        name = " ".join(str(tool_input.get("name") or "").split()).strip()
        if not name or len(name) > 40:
            return ToolOutcome("name must be a short, non-empty name.", is_error=True)
        faces = list(getattr(self._state, "current_faces", None) or [])
        if not faces:
            return ToolOutcome("No face is in view right now.", is_error=True)
        frame = await self._camera.get_frame()
        if frame is None:
            return ToolOutcome("The camera returned no image.", is_error=True)
        primary = max(faces, key=lambda detection: detection.area)
        if not await self._face_recognizer.register_face(name, frame, primary):
            return ToolOutcome("Saving that face failed; ask them to face the camera.", is_error=True)
        return ToolOutcome(f"Saved {name}'s face.")

    async def _tool_use_skill(self, tool_input: dict, user_name: str) -> ToolOutcome:
        phrase = str(tool_input.get("phrase") or "").strip()
        match = self._skill_manager.match_command(phrase) if phrase else None
        if match is None:
            return ToolOutcome(
                "No skill matches that phrase. Use one of the listed trigger phrases.",
                is_error=True,
            )
        command, _trigger = match
        if command.skill_name == "mobility_control" and not self.can_drive:
            return ToolOutcome("Driving is turned off for conversation.", is_error=True)
        result = await command.handler(phrase, user_name)
        return ToolOutcome(result or "Done.")

    async def _tool_end_conversation(self, tool_input: dict, user_name: str) -> ToolOutcome:
        self.conversation_ended = True
        return ToolOutcome("Conversation closed after this reply.")


def _encode_jpeg(frame) -> Optional[bytes]:
    """Encode an OpenCV BGR frame as a compact JPEG for Claude."""
    try:
        import cv2
    except ImportError:
        logger.error("OpenCV is required to send camera photos")
        return None
    height, width = frame.shape[:2]
    if width > MAX_PHOTO_WIDTH:
        scale = MAX_PHOTO_WIDTH / width
        frame = cv2.resize(frame, (MAX_PHOTO_WIDTH, int(height * scale)))
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
    return buffer.tobytes() if ok else None
