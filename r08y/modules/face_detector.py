"""Face detection module for R08Y.

Provides Haar cascade (fast) and DNN (accurate) face detectors behind a
common interface. Downscales frames before detection for performance on Pi 4.
"""

import asyncio
import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import List, Optional

import cv2
import numpy as np

from r08y.config import FaceDetectionConfig

logger = logging.getLogger(__name__)


@dataclass
class FaceDetection:
    """A detected face bounding box."""
    x: int
    y: int
    w: int
    h: int
    confidence: float = 1.0

    @property
    def center(self) -> tuple[int, int]:
        return (self.x + self.w // 2, self.y + self.h // 2)

    @property
    def area(self) -> int:
        return self.w * self.h


class FaceDetector(ABC):
    """Abstract base for face detectors."""

    @abstractmethod
    def detect_sync(self, frame: np.ndarray) -> List[FaceDetection]:
        """Detect faces in a frame (blocking)."""
        ...

    async def detect(self, frame: np.ndarray) -> List[FaceDetection]:
        """Detect faces in a frame (async, runs in thread)."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, self.detect_sync, frame)


class HaarFaceDetector(FaceDetector):
    """Fast face detection using Haar cascades.

    ~15-20 FPS on Pi 4 at 320x240. Good for real-time tracking.
    """

    def __init__(self, config: FaceDetectionConfig):
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="haar")
        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        self._cascade = cv2.CascadeClassifier(cascade_path)
        if self._cascade.empty():
            raise RuntimeError(f"Failed to load Haar cascade from {cascade_path}")
        logger.info("Haar face detector initialized")

    def detect_sync(self, frame: np.ndarray) -> List[FaceDetection]:
        # Downscale for performance
        h, w = frame.shape[:2]
        target_w = self._config.detection_width
        scale = w / target_w if w > target_w else 1.0
        if scale > 1.0:
            small = cv2.resize(frame, (target_w, int(h / scale)))
        else:
            small = frame

        gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        rects = self._cascade.detectMultiScale(
            gray,
            scaleFactor=self._config.scale_factor,
            minNeighbors=self._config.min_neighbors,
            minSize=(30, 30),
        )

        detections = []
        for (rx, ry, rw, rh) in rects:
            detections.append(FaceDetection(
                x=int(rx * scale),
                y=int(ry * scale),
                w=int(rw * scale),
                h=int(rh * scale),
            ))
        return detections


class DNNFaceDetector(FaceDetector):
    """Accurate face detection using OpenCV DNN with a pre-trained model.

    ~3-5 FPS on Pi 4. Better for recognition accuracy when tracking speed
    is less critical.
    """

    # OpenCV ships these model files with the DNN module
    MODEL_FILE = "opencv_face_detector_uint8.pb"
    CONFIG_FILE = "opencv_face_detector.pbtxt"

    def __init__(self, config: FaceDetectionConfig):
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="dnn")
        try:
            self._net = cv2.dnn.readNetFromTensorflow(self.MODEL_FILE, self.CONFIG_FILE)
            logger.info("DNN face detector initialized")
        except cv2.error:
            logger.warning(
                "DNN model files not found (%s, %s). "
                "Download them or use method=haar in config.",
                self.MODEL_FILE, self.CONFIG_FILE,
            )
            raise

    def detect_sync(self, frame: np.ndarray) -> List[FaceDetection]:
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            cv2.resize(frame, (300, 300)),
            1.0, (300, 300), (104.0, 177.0, 123.0),
        )
        self._net.setInput(blob)
        detections_raw = self._net.forward()

        detections = []
        for i in range(detections_raw.shape[2]):
            confidence = detections_raw[0, 0, i, 2]
            if confidence > self._config.confidence_threshold:
                box = detections_raw[0, 0, i, 3:7] * np.array([w, h, w, h])
                x1, y1, x2, y2 = box.astype(int)
                detections.append(FaceDetection(
                    x=max(0, x1),
                    y=max(0, y1),
                    w=max(0, x2 - x1),
                    h=max(0, y2 - y1),
                    confidence=float(confidence),
                ))
        return detections


def create_face_detector(config: FaceDetectionConfig) -> Optional[FaceDetector]:
    """Factory: create the configured face detector."""
    if config.method == "dnn":
        try:
            return DNNFaceDetector(config)
        except Exception:
            logger.warning("Falling back to Haar detector")
            return HaarFaceDetector(config)
    return HaarFaceDetector(config)
