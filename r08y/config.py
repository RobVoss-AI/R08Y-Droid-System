"""Configuration management for R08Y Droid System.

Loads typed configuration from config.yaml with environment variable overrides
for sensitive values (API keys).
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


@dataclass
class CameraConfig:
    device_index: int = 0
    width: int = 640
    height: int = 480
    fps: int = 30


@dataclass
class FaceDetectionConfig:
    method: str = "haar"
    scale_factor: float = 1.3
    min_neighbors: int = 5
    detection_width: int = 320
    confidence_threshold: float = 0.5


@dataclass
class FaceRecognitionConfig:
    db_path: str = "data/faces.db"
    tolerance: float = 0.6
    max_encodings_per_person: int = 5
    cache_ttl_seconds: float = 5.0


@dataclass
class ServoConfig:
    enabled: bool = True
    driver: str = "auto"
    i2c_address: int = 0x40
    pan_channel: int = 0
    tilt_channel: int = 1
    pan_min: int = 30
    pan_max: int = 150
    tilt_min: int = 30
    tilt_max: int = 150
    home_pan: int = 90
    home_tilt: int = 90
    speed: int = 120


@dataclass
class TrackerConfig:
    pid_kp: float = 0.05
    pid_ki: float = 0.01
    pid_kd: float = 0.02
    dead_zone: float = 0.05
    patrol_speed: float = 30.0
    no_face_timeout: float = 5.0


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 512
    device_index: Optional[int] = None
    rms_threshold: int = 500
    silence_duration: float = 1.5
    max_record_seconds: int = 30


@dataclass
class WakeWordConfig:
    engine: str = "porcupine"
    porcupine_access_key: str = ""
    keyword_path: Optional[str] = None
    sensitivity: float = 0.5


@dataclass
class STTConfig:
    provider: str = "groq"
    openai_api_key: str = ""
    groq_api_key: str = ""
    model: str = "whisper-large-v3"
    language: str = "en"


@dataclass
class AIConfig:
    api_key: str = ""
    model: str = "claude-sonnet-4-20250514"
    max_history_turns: int = 20
    system_prompt: str = (
        "You are R08Y, a friendly and helpful droid assistant. You have a physical "
        "body with a camera and can see the people you talk to. You recognize faces "
        "and remember conversations. Keep responses conversational and concise — "
        "you're speaking out loud, not writing an essay."
    )
    conversations_db_path: str = "data/conversations.db"


@dataclass
class TTSConfig:
    voice: str = "en-US-GuyNeural"
    rate: str = "+0%"
    volume: str = "+0%"
    playback_command: str = "aplay"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    file: Optional[str] = None


@dataclass
class R08YConfig:
    camera: CameraConfig = field(default_factory=CameraConfig)
    face_detection: FaceDetectionConfig = field(default_factory=FaceDetectionConfig)
    face_recognition: FaceRecognitionConfig = field(default_factory=FaceRecognitionConfig)
    servos: ServoConfig = field(default_factory=ServoConfig)
    tracker: TrackerConfig = field(default_factory=TrackerConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


def _merge_dict_to_dataclass(dc_class, data: dict):
    """Create a dataclass instance from a dict, ignoring unknown keys."""
    if data is None:
        return dc_class()
    valid_fields = {f.name for f in dc_class.__dataclass_fields__.values()}
    filtered = {k: v for k, v in data.items() if k in valid_fields}
    return dc_class(**filtered)


def _apply_env_overrides(config: R08YConfig) -> None:
    """Override sensitive config values with environment variables."""
    env_map = {
        "R08Y_ANTHROPIC_API_KEY": ("ai", "api_key"),
        "R08Y_OPENAI_API_KEY": ("stt", "openai_api_key"),
        "R08Y_GROQ_API_KEY": ("stt", "groq_api_key"),
        "R08Y_PICOVOICE_KEY": ("wake_word", "porcupine_access_key"),
    }
    for env_var, (section, attr) in env_map.items():
        value = os.environ.get(env_var)
        if value:
            setattr(getattr(config, section), attr, value)


def load_config(config_path: str = "config.yaml") -> R08YConfig:
    """Load configuration from YAML file with environment variable overrides.

    Falls back to defaults if config file doesn't exist.
    """
    data = {}
    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}

    config = R08YConfig(
        camera=_merge_dict_to_dataclass(CameraConfig, data.get("camera")),
        face_detection=_merge_dict_to_dataclass(FaceDetectionConfig, data.get("face_detection")),
        face_recognition=_merge_dict_to_dataclass(FaceRecognitionConfig, data.get("face_recognition")),
        servos=_merge_dict_to_dataclass(ServoConfig, data.get("servos")),
        tracker=_merge_dict_to_dataclass(TrackerConfig, data.get("tracker")),
        audio=_merge_dict_to_dataclass(AudioConfig, data.get("audio")),
        wake_word=_merge_dict_to_dataclass(WakeWordConfig, data.get("wake_word")),
        stt=_merge_dict_to_dataclass(STTConfig, data.get("stt")),
        ai=_merge_dict_to_dataclass(AIConfig, data.get("ai")),
        tts=_merge_dict_to_dataclass(TTSConfig, data.get("tts")),
        logging=_merge_dict_to_dataclass(LoggingConfig, data.get("logging")),
    )

    _apply_env_overrides(config)
    return config
