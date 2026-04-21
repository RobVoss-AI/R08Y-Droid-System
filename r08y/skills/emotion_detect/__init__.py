"""Emotion Detection skill — read facial expressions.

Analyzes detected faces for basic emotions using a simple approach based on
facial landmarks. For production accuracy, swap in a dedicated model like
FER or DeepFace.

Events emitted: emotion.detected
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Optional

import cv2
import numpy as np

from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest, VoiceCommand
from r08y.modules.face_detector import FaceDetection


class Skill(BaseSkill):
    def __init__(self):
        super().__init__()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="emotion")
        self._latest_emotion: Optional[str] = None
        self._latest_confidence: float = 0.0

    @property
    def manifest(self) -> SkillManifest:
        return SkillManifest(
            name="emotion_detect",
            version="1.0.0",
            description="Detect facial expressions from camera",
            requires={"camera"},
            pipeline_hooks=[PipelineHook.VISION_DETECTIONS],
            commands=[
                VoiceCommand(triggers=["how do i look", "what's my mood", "read my expression", "how am i feeling"], handler="cmd_read"),
            ],
            emits_events=["emotion.detected"],
        )

    def _analyze_emotion_sync(self, face_roi: np.ndarray) -> Optional[Dict]:
        """Analyze emotion from a face ROI using histogram-based heuristics.

        This is a simplified approach. For better accuracy, integrate a
        dedicated model (FER2013, DeepFace, etc.) by replacing this method.
        """
        try:
            gray = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
            h, w = gray.shape

            # Simple heuristic: analyze brightness distribution
            upper_half = gray[:h // 2, :]
            lower_half = gray[h // 2:, :]

            upper_mean = float(np.mean(upper_half))
            lower_mean = float(np.mean(lower_half))
            overall_std = float(np.std(gray))

            # Very rough heuristics (placeholder for a real model)
            if overall_std > 60:
                return {"label": "surprised", "confidence": 0.5}
            elif lower_mean > upper_mean + 10:
                return {"label": "happy", "confidence": 0.5}
            elif upper_mean > lower_mean + 15:
                return {"label": "sad", "confidence": 0.4}
            else:
                return {"label": "neutral", "confidence": 0.6}
        except Exception:
            return None

    async def on_vision_detections(self, frame: np.ndarray, detections: list) -> None:
        if not detections:
            return

        primary = max(detections, key=lambda d: d.area)
        h, w = frame.shape[:2]
        x1 = max(0, primary.x)
        y1 = max(0, primary.y)
        x2 = min(w, primary.x + primary.w)
        y2 = min(h, primary.y + primary.h)
        face_roi = frame[y1:y2, x1:x2]

        if face_roi.size == 0:
            return

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(self._executor, self._analyze_emotion_sync, face_roi)

        if result:
            self._latest_emotion = result["label"]
            self._latest_confidence = result["confidence"]
            await self.ctx.event_bus.emit(
                "emotion.detected", source=self.name,
                emotion=result["label"], confidence=result["confidence"],
            )

    async def cmd_read(self, text: str, user_name: str) -> Optional[str]:
        if not self._latest_emotion:
            return "I can't read your expression right now. Make sure I can see your face."
        return f"You look {self._latest_emotion}."

    async def stop(self):
        await super().stop()
        self._executor.shutdown(wait=False)
