"""Async camera capture module for R08Y.

Wraps OpenCV VideoCapture with an asyncio-friendly interface by running
blocking reads in a ThreadPoolExecutor. Always holds only the latest frame
to prevent queue buildup.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import cv2
import numpy as np

from r08y.config import CameraConfig

logger = logging.getLogger(__name__)


class CameraCapture:
    """Async wrapper around cv2.VideoCapture for USB webcam."""

    def __init__(self, config: CameraConfig):
        self._config = config
        self._cap: Optional[cv2.VideoCapture] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera")
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_time: float = 0.0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.available = False
        self.fps_actual: float = 0.0

    def _open_camera(self) -> bool:
        """Open the camera device (runs in thread)."""
        try:
            self._cap = cv2.VideoCapture(self._config.device_index)
            if not self._cap.isOpened():
                logger.error("Failed to open camera at index %d", self._config.device_index)
                return False
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._config.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._config.height)
            self._cap.set(cv2.CAP_PROP_FPS, self._config.fps)
            actual_w = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            actual_h = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            logger.info("Camera opened: %dx%d at index %d", actual_w, actual_h, self._config.device_index)
            return True
        except Exception:
            logger.exception("Error opening camera")
            return False

    def _read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        """Read a single frame (runs in thread)."""
        if self._cap is None or not self._cap.isOpened():
            return False, None
        return self._cap.read()

    async def start(self) -> None:
        """Start the camera capture loop."""
        loop = asyncio.get_event_loop()
        opened = await loop.run_in_executor(self._executor, self._open_camera)
        if not opened:
            logger.warning("Camera unavailable — vision pipeline will be disabled")
            return

        self.available = True
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())
        logger.info("Camera capture started")

    async def _capture_loop(self) -> None:
        """Continuously capture frames in the background."""
        loop = asyncio.get_event_loop()
        frame_count = 0
        fps_start = time.monotonic()

        while self._running:
            ret, frame = await loop.run_in_executor(self._executor, self._read_frame)
            if ret and frame is not None:
                self._latest_frame = frame
                self._frame_time = time.monotonic()
                frame_count += 1

                elapsed = time.monotonic() - fps_start
                if elapsed >= 2.0:
                    self.fps_actual = frame_count / elapsed
                    frame_count = 0
                    fps_start = time.monotonic()
            else:
                logger.warning("Camera read failed — attempting reconnect")
                await asyncio.sleep(1.0)
                await loop.run_in_executor(self._executor, self._open_camera)

    async def get_frame(self) -> Optional[np.ndarray]:
        """Get the most recent camera frame.

        Returns None if no frame is available or camera is not running.
        """
        return self._latest_frame

    @property
    def frame_age(self) -> float:
        """Seconds since the latest frame was captured."""
        if self._frame_time == 0:
            return float("inf")
        return time.monotonic() - self._frame_time

    async def stop(self) -> None:
        """Stop capture and release the camera."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._cap and self._cap.isOpened():
            self._cap.release()
            logger.info("Camera released")
        self._executor.shutdown(wait=False)
        self.available = False
