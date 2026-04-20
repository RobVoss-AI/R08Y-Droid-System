"""Tests for the face detection module."""

import numpy as np

from r08y.config import FaceDetectionConfig
from r08y.modules.face_detector import FaceDetection, HaarFaceDetector


def test_face_detection_dataclass():
    det = FaceDetection(x=100, y=50, w=200, h=200, confidence=0.95)
    assert det.center == (200, 150)
    assert det.area == 40000


def test_haar_detector_no_faces():
    """Haar detector should return empty list for a blank frame."""
    config = FaceDetectionConfig(method="haar")
    detector = HaarFaceDetector(config)
    blank = np.zeros((480, 640, 3), dtype=np.uint8)
    detections = detector.detect_sync(blank)
    assert detections == []


def test_haar_detector_initialization():
    config = FaceDetectionConfig(method="haar", scale_factor=1.2, min_neighbors=4)
    detector = HaarFaceDetector(config)
    assert detector._cascade is not None
    assert not detector._cascade.empty()


if __name__ == "__main__":
    test_face_detection_dataclass()
    test_haar_detector_no_faces()
    test_haar_detector_initialization()
    print("All face detection tests passed!")
