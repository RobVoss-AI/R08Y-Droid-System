"""Tests for the face detection module."""

import numpy as np

from r08y.config import FaceDetectionConfig
from r08y.modules import face_detector
from r08y.modules.face_detector import (
    FaceDetection,
    HaarFaceDetector,
    UnavailableFaceDetector,
    create_face_detector,
    resolve_haar_cascade_path,
)


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


def test_haar_path_resolver_supports_debian_opencv_data(monkeypatch, tmp_path):
    cascade_path = tmp_path / "haarcascade_frontalface_default.xml"
    cascade_path.write_text("cascade")
    monkeypatch.delattr(face_detector.cv2, "data", raising=False)
    monkeypatch.setattr(face_detector.cv2, "__file__", str(tmp_path / "cv2.so"))
    monkeypatch.setattr(
        face_detector,
        "SYSTEM_HAAR_CASCADE_PATHS",
        (cascade_path,),
    )

    assert resolve_haar_cascade_path() == cascade_path


def test_missing_haar_data_disables_detection_without_crashing(monkeypatch):
    monkeypatch.setattr(face_detector, "resolve_haar_cascade_path", lambda _path: None)

    detector = create_face_detector(FaceDetectionConfig(method="haar"))

    assert isinstance(detector, UnavailableFaceDetector)
    assert not detector.available
    assert "Install opencv-data" in detector.detail


if __name__ == "__main__":
    test_face_detection_dataclass()
    test_haar_detector_no_faces()
    test_haar_detector_initialization()
    print("All face detection tests passed!")
