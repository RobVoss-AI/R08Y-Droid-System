"""Object Detection skill — identify common objects using MobileNet SSD.

Hooks into the vision pipeline to run object detection (throttled to 1 FPS).
Voice commands: "what do you see", "is there a [object]"
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import cv2
import numpy as np

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand

# MobileNet SSD class labels
MOBILENET_CLASSES = [
    "background", "aeroplane", "bicycle", "bird", "boat", "bottle", "bus",
    "car", "cat", "chair", "cow", "diningtable", "dog", "horse",
    "motorbike", "person", "pottedplant", "sheep", "sofa", "train", "tvmonitor",
]


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._net = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="objdet")
        self._latest_objects: List[Dict] = []
        self._confidence_threshold = 0.4

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="object_detect",
            version="1.0.0",
            description="Detect common objects using MobileNet SSD",
            requires={"camera"},
            pipeline_hooks=[PipelineHook.VISION_FRAME],
            vision_frame_interval=1.0,
            commands=[
                VoiceCommand(triggers=["what do you see", "what's around", "describe the scene", "describe what you see"], handler="cmd_describe"),
                VoiceCommand(triggers=["do you see a", "is there a", "can you see a"], handler="cmd_find"),
                VoiceCommand(triggers=["how many people", "count people"], handler="cmd_count_people"),
            ],
            emits_events=["objects.detected"],
        )

    async def start(self):
        await super().start()
        try:
            # Load MobileNet SSD (ships with OpenCV or downloadable)
            prototxt = cv2.data.haarcascades.replace("haarcascade_", "").rstrip("/") + "/../dnn/MobileNetSSD_deploy.prototxt"
            model = cv2.data.haarcascades.replace("haarcascade_", "").rstrip("/") + "/../dnn/MobileNetSSD_deploy.caffemodel"
            # Fallback: try loading from data directory
            from pathlib import Path
            data = Path(self.ctx.data_dir)
            proto_path = data / "MobileNetSSD_deploy.prototxt"
            model_path = data / "MobileNetSSD_deploy.caffemodel"
            if proto_path.exists() and model_path.exists():
                self._net = cv2.dnn.readNetFromCaffe(str(proto_path), str(model_path))
                self.ctx.logger.info("Object detection model loaded from data dir")
            else:
                self.ctx.logger.warning(
                    "MobileNet SSD model not found. Download to %s to enable object detection.",
                    self.ctx.data_dir,
                )
        except Exception:
            self.ctx.logger.exception("Failed to load object detection model")

    def _detect_sync(self, frame: np.ndarray) -> List[Dict]:
        if self._net is None:
            return []
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(cv2.resize(frame, (300, 300)), 0.007843, (300, 300), 127.5)
        self._net.setInput(blob)
        detections = self._net.forward()

        objects = []
        for i in range(detections.shape[2]):
            confidence = float(detections[0, 0, i, 2])
            if confidence > self._confidence_threshold:
                class_id = int(detections[0, 0, i, 1])
                if 0 < class_id < len(MOBILENET_CLASSES):
                    objects.append({
                        "label": MOBILENET_CLASSES[class_id],
                        "confidence": confidence,
                    })
        return objects

    async def on_vision_frame(self, frame: np.ndarray) -> None:
        loop = asyncio.get_event_loop()
        objects = await loop.run_in_executor(self._executor, self._detect_sync, frame)
        if objects:
            self._latest_objects = objects
            await self.ctx.event_bus.emit("objects.detected", source=self.name, objects=objects)

    async def cmd_describe(self, text: str, user_name: str) -> Optional[str]:
        if not self._latest_objects:
            if self._net is None:
                return "Object detection model isn't loaded. I can only detect faces right now."
            return "I don't see any notable objects right now."
        # Deduplicate and count
        counts: Dict[str, int] = {}
        for obj in self._latest_objects:
            label = obj["label"]
            counts[label] = counts.get(label, 0) + 1
        parts = [f"{count} {label}{'s' if count > 1 else ''}" for label, count in counts.items()]
        return f"I can see: {', '.join(parts)}."

    async def cmd_find(self, text: str, user_name: str) -> Optional[str]:
        text_lower = text.lower()
        for obj in self._latest_objects:
            if obj["label"] in text_lower:
                return f"Yes, I can see a {obj['label']}!"
        return "I don't see that right now."

    async def cmd_count_people(self, text: str, user_name: str) -> Optional[str]:
        count = sum(1 for obj in self._latest_objects if obj["label"] == "person")
        if count == 0:
            return "I don't see any people right now."
        return f"I can see {count} {'person' if count == 1 else 'people'}."

    async def stop(self):
        await super().stop()
        self._executor.shutdown(wait=False)
