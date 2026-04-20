"""Face recognition module for R08Y.

Uses dlib-based face_recognition library for 128D face encodings stored in
SQLite. Supports registering, recognizing, and deleting known faces with
a TTL cache to avoid expensive re-encoding every frame.
"""

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiosqlite
import numpy as np

from r08y.config import FaceRecognitionConfig
from r08y.modules.face_detector import FaceDetection

logger = logging.getLogger(__name__)

# face_recognition is an optional heavy dependency
try:
    import face_recognition as fr
    FR_AVAILABLE = True
except ImportError:
    FR_AVAILABLE = False
    logger.warning("face_recognition not installed — recognition disabled")


@dataclass
class RecognitionResult:
    """Result of a face recognition attempt."""
    name: str
    confidence: float  # 0.0 = perfect match, 1.0 = no match
    is_known: bool

    @staticmethod
    def unknown() -> "RecognitionResult":
        return RecognitionResult(name="unknown", confidence=1.0, is_known=False)


class FaceRecognizer:
    """Recognizes known faces using 128D dlib encodings stored in SQLite."""

    def __init__(self, config: FaceRecognitionConfig):
        self._config = config
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face-rec")
        self._db: Optional[aiosqlite.Connection] = None
        self._known_encodings: Dict[str, List[np.ndarray]] = {}
        self._cache: Dict[str, Tuple[RecognitionResult, float]] = {}  # key -> (result, timestamp)
        self.available = False

    async def start(self) -> None:
        """Initialize the database and load known face encodings."""
        if not FR_AVAILABLE:
            logger.warning("Face recognition unavailable — face_recognition library not installed")
            return

        db_path = Path(self._config.db_path)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        self._db = await aiosqlite.connect(str(db_path))
        await self._db.execute("""
            CREATE TABLE IF NOT EXISTS faces (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                encoding BLOB NOT NULL,
                created_at REAL NOT NULL
            )
        """)
        await self._db.commit()
        await self._load_known_faces()
        self.available = True
        logger.info("Face recognizer initialized with %d known people", len(self._known_encodings))

    async def _load_known_faces(self) -> None:
        """Load all face encodings from the database into memory."""
        self._known_encodings.clear()
        async with self._db.execute("SELECT name, encoding FROM faces") as cursor:
            async for row in cursor:
                name = row[0]
                encoding = np.frombuffer(row[1], dtype=np.float64)
                if name not in self._known_encodings:
                    self._known_encodings[name] = []
                self._known_encodings[name].append(encoding)

    def _encode_face_sync(self, frame: np.ndarray, detection: FaceDetection) -> Optional[np.ndarray]:
        """Compute 128D face encoding (blocking, CPU-intensive)."""
        # face_recognition uses (top, right, bottom, left) format
        top = detection.y
        right = detection.x + detection.w
        bottom = detection.y + detection.h
        left = detection.x
        face_location = [(top, right, bottom, left)]

        rgb_frame = frame[:, :, ::-1]  # BGR to RGB
        encodings = fr.face_encodings(rgb_frame, known_face_locations=face_location)
        if encodings:
            return encodings[0]
        return None

    def _recognize_sync(self, encoding: np.ndarray) -> RecognitionResult:
        """Compare an encoding against all known faces (blocking)."""
        if not self._known_encodings:
            return RecognitionResult.unknown()

        best_name = "unknown"
        best_distance = float("inf")

        for name, encodings in self._known_encodings.items():
            distances = fr.face_distance(encodings, encoding)
            min_distance = float(np.min(distances))
            if min_distance < best_distance:
                best_distance = min_distance
                best_name = name

        if best_distance <= self._config.tolerance:
            return RecognitionResult(name=best_name, confidence=best_distance, is_known=True)
        return RecognitionResult.unknown()

    async def recognize(self, frame: np.ndarray, detection: FaceDetection) -> RecognitionResult:
        """Recognize a detected face. Uses cache to avoid re-encoding recently seen faces."""
        if not self.available:
            return RecognitionResult.unknown()

        # Check cache using face position as key
        cache_key = f"{detection.x//20}_{detection.y//20}"
        cached = self._cache.get(cache_key)
        if cached:
            result, timestamp = cached
            if time.monotonic() - timestamp < self._config.cache_ttl_seconds:
                return result

        loop = asyncio.get_event_loop()
        encoding = await loop.run_in_executor(self._executor, self._encode_face_sync, frame, detection)
        if encoding is None:
            return RecognitionResult.unknown()

        result = await loop.run_in_executor(self._executor, self._recognize_sync, encoding)

        # Update cache
        self._cache[cache_key] = (result, time.monotonic())
        # Prune stale cache entries
        now = time.monotonic()
        self._cache = {
            k: v for k, v in self._cache.items()
            if now - v[1] < self._config.cache_ttl_seconds * 3
        }

        return result

    async def register_face(self, name: str, frame: np.ndarray, detection: FaceDetection) -> bool:
        """Register a new face encoding for a person.

        Stores up to max_encodings_per_person encodings. Returns True on success.
        """
        if not self.available:
            return False

        loop = asyncio.get_event_loop()
        encoding = await loop.run_in_executor(self._executor, self._encode_face_sync, frame, detection)
        if encoding is None:
            logger.warning("Failed to encode face for registration")
            return False

        # Check if we're at the encoding limit for this person
        existing = self._known_encodings.get(name, [])
        if len(existing) >= self._config.max_encodings_per_person:
            # Remove the oldest encoding
            await self._db.execute(
                "DELETE FROM faces WHERE id = (SELECT id FROM faces WHERE name = ? ORDER BY created_at ASC LIMIT 1)",
                (name,),
            )

        await self._db.execute(
            "INSERT INTO faces (name, encoding, created_at) VALUES (?, ?, ?)",
            (name, encoding.tobytes(), time.time()),
        )
        await self._db.commit()
        await self._load_known_faces()

        logger.info("Registered face for '%s' (total encodings: %d)", name, len(self._known_encodings.get(name, [])))
        return True

    async def get_known_names(self) -> List[str]:
        """List all registered face names."""
        return list(self._known_encodings.keys())

    async def delete_face(self, name: str) -> bool:
        """Delete all encodings for a person."""
        if not self.available:
            return False
        await self._db.execute("DELETE FROM faces WHERE name = ?", (name,))
        await self._db.commit()
        await self._load_known_faces()
        logger.info("Deleted face data for '%s'", name)
        return True

    async def stop(self) -> None:
        """Close the database connection."""
        if self._db:
            await self._db.close()
        self._executor.shutdown(wait=False)
        self.available = False
