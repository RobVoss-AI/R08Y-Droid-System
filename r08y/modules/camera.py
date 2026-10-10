"""Async camera capture with Picamera2 and OpenCV backends."""

from __future__ import annotations

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
    """Keep the latest camera frame without allowing a backlog to build."""

    def __init__(self, config: CameraConfig):
        self._config = config
        self._cap = None
        self._backend: Optional[str] = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera")
        self._latest_frame: Optional[np.ndarray] = None
        self._frame_time = 0.0
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.available = False
        self.fps_actual = 0.0

    def _open_picamera2(self) -> bool:
        try:
            from picamera2 import Picamera2
            from libcamera import Transform

            camera = Picamera2()
            configuration = camera.create_video_configuration(
                main={"size": (self._config.width, self._config.height), "format": "RGB888"},
                transform=Transform(
                    hflip=1 if self._config.horizontal_flip else 0,
                    vflip=1 if self._config.vertical_flip else 0,
                ),
                controls={"FrameRate": self._config.fps},
            )
            camera.configure(configuration)
            camera.start()
            self._cap = camera
            self._backend = "picamera2"
            logger.info("Picamera2 opened: %dx%d", self._config.width, self._config.height)
            return True
        except Exception:
            logger.debug("Picamera2 backend unavailable", exc_info=True)
            return False

    def _open_opencv(self) -> bool:
        try:
            source = self._config.device_path or self._config.device_index
            self._cap = cv2.VideoCapture(source)
            if not self._cap.isOpened():
                logger.error("Failed to open camera source %s", source)
                self._cap.release()
                self._cap = None
                return False
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self._config.width)
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self._config.height)
            self._cap.set(cv2.CAP_PROP_FPS, self._config.fps)
            self._backend = "opencv"
            logger.info(
                "OpenCV camera opened: %dx%d from %s",
                int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
                int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
                source,
            )
            return True
        except Exception:
            logger.exception("OpenCV camera initialization failed")
            return False

    def _close_sync(self) -> None:
        if self._cap is None:
            return
        try:
            if self._backend == "picamera2":
                self._cap.stop()
                self._cap.close()
            elif self._cap.isOpened():
                self._cap.release()
        except Exception:
            logger.debug("Camera close failed", exc_info=True)
        finally:
            self._cap = None
            self._backend = None

    def _open_camera(self) -> bool:
        self._close_sync()
        backend = self._config.backend.lower()
        if backend not in {"auto", "picamera2", "opencv"}:
            logger.error("Unknown camera backend: %s", backend)
            return False
        if backend in {"auto", "picamera2"} and self._open_picamera2():
            return True
        if backend == "picamera2":
            return False
        return self._open_opencv()

    def _read_frame(self) -> tuple[bool, Optional[np.ndarray]]:
        if self._cap is None:
            return False, None
        if self._backend == "picamera2":
            frame = self._cap.capture_array("main")
            if frame is None:
                return False, None
            # R08Y's vision pipeline consumes OpenCV BGR frames.
            return True, cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
        if not self._cap.isOpened():
            return False, None
        return self._cap.read()

    async def start(self) -> None:
        if not self._config.enabled:
            logger.info("Camera disabled in config")
            return
        loop = asyncio.get_running_loop()
        if not await loop.run_in_executor(self._executor, self._open_camera):
            logger.warning("Camera unavailable; vision pipeline disabled")
            return
        self.available = True
        self._running = True
        self._task = asyncio.create_task(self._capture_loop())

    async def _capture_loop(self) -> None:
        loop = asyncio.get_running_loop()
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
                logger.warning("Camera read failed; attempting reconnect")
                await asyncio.sleep(1.0)
                if not await loop.run_in_executor(self._executor, self._open_camera):
                    await asyncio.sleep(2.0)

    async def get_frame(self) -> Optional[np.ndarray]:
        return self._latest_frame

    @property
    def frame_age(self) -> float:
        if self._frame_time == 0:
            return float("inf")
        return time.monotonic() - self._frame_time

    @property
    def backend(self) -> Optional[str]:
        return self._backend

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await asyncio.get_running_loop().run_in_executor(self._executor, self._close_sync)
        self._executor.shutdown(wait=False)
        self.available = False
