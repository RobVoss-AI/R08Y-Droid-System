"""Tests for the camera capture module."""

import asyncio
import numpy as np
from unittest.mock import MagicMock, patch

from r08y.config import CameraConfig
from r08y.modules.camera import CameraCapture


def test_camera_config_defaults():
    config = CameraConfig()
    assert config.device_index == 0
    assert config.width == 640
    assert config.height == 480
    assert config.fps == 30


async def _test_camera_lifecycle():
    """Test camera start/stop with mocked OpenCV."""
    config = CameraConfig(device_index=0, backend="opencv")

    mock_cap = MagicMock()
    mock_cap.isOpened.return_value = True
    mock_cap.read.return_value = (True, np.zeros((480, 640, 3), dtype=np.uint8))
    mock_cap.get.return_value = 640

    with patch("cv2.VideoCapture", return_value=mock_cap):
        camera = CameraCapture(config)
        await camera.start()
        assert camera.available

        # Wait a moment for capture loop to run
        await asyncio.sleep(0.2)

        frame = await camera.get_frame()
        assert frame is not None
        assert frame.shape == (480, 640, 3)

        await camera.stop()
        assert not camera.available


def test_camera_lifecycle():
    asyncio.run(_test_camera_lifecycle())


if __name__ == "__main__":
    test_camera_config_defaults()
    test_camera_lifecycle()
    print("All camera tests passed!")
