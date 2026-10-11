"""Configuration management for R08Y Droid System.

Loads typed configuration from config.yaml with environment variable overrides
for sensitive values (API keys).
"""

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Union

import yaml


@dataclass
class CameraConfig:
    enabled: bool = True
    backend: str = "auto"
    device_index: Union[int, str] = 0
    device_path: Optional[str] = None
    width: int = 640
    height: int = 480
    fps: int = 30
    horizontal_flip: bool = False
    vertical_flip: bool = False


@dataclass
class FaceDetectionConfig:
    method: str = "haar"
    cascade_path: Optional[str] = None
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
    freenove_library_path: Optional[str] = None
    i2c_address: int = 0x40
    pan_channel: int = 0
    tilt_channel: int = 1
    gpio_pan_pin: Optional[int] = None
    gpio_tilt_pin: Optional[int] = None
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
class ExpressionConfig:
    enabled: bool = True
    update_interval: float = 0.05
    max_step_degrees: float = 3.0
    gesture_scale: float = 1.0
    ambient_enabled: bool = True
    idle_sway_degrees: float = 1.5
    thinking_sway_degrees: float = 3.0
    speaking_bob_degrees: float = 1.5
    queue_size: int = 8


@dataclass
class AudioConfig:
    sample_rate: int = 16000
    channels: int = 1
    chunk_size: int = 512
    device_index: Optional[int] = None
    device_name: str = "XVF3800"
    require_device_match: bool = True
    capture_backend: str = "auto"
    hardware_sample_rate: Optional[int] = 16000
    hardware_channels: Optional[int] = 2
    rms_threshold: int = 500
    silence_duration: float = 1.5
    max_record_seconds: int = 30


@dataclass
class WakeWordConfig:
    engine: str = "porcupine"
    phrase: str = "ROBY"
    porcupine_access_key: str = ""
    keyword_path: Optional[str] = "/opt/r08y/wake_words/roby.ppn"
    sensitivity: float = 0.5
    allow_rms_fallback: bool = False


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
    model: str = "claude-sonnet-5-5"
    max_history_turns: int = 20
    system_prompt: str = (
        "You are R08Y, a friendly and helpful droid assistant. You have a physical "
        "body with a camera and can see the people you talk to. You recognize faces "
        "and remember conversations. Keep responses conversational and concise — "
        "you're speaking out loud, not writing an essay."
    )
    spoken_style: str = (
        "Speak with calm warmth and a lightly playful droid personality. Use natural "
        "contractions and plain spoken language. Keep most replies to one or two short "
        "sentences and under 30 words when practical. Help first, then add at most one "
        "understated witty aside when it fits. Avoid filler, monologues, sarcasm, and "
        "theatrical robot catchphrases."
    )
    conversations_db_path: str = "data/conversations.db"


@dataclass
class TTSConfig:
    voice: str = "en-US-AndrewMultilingualNeural"
    rate: str = "+1%"
    pitch: str = "+2Hz"
    volume: str = "+0%"
    playback_command: str = "pw-play"
    output_device_name: str = "XVF3800"
    output_target: Optional[str] = None
    set_pipewire_default: bool = True
    require_output_device_match: bool = True


@dataclass
class FreenoveConfig:
    """Location of the official Freenove server modules.

    R08Y imports the installed Freenove modules instead of copying their
    board-specific channel implementation into this project.
    """

    library_path: Optional[str] = None
    auto_discover: bool = True


@dataclass
class DistanceConfig:
    enabled: bool = True
    driver: str = "auto"
    freenove_library_path: Optional[str] = None
    trigger_pin: Optional[int] = None
    echo_pin: Optional[int] = None
    max_distance_m: float = 3.0
    timeout_seconds: float = 0.2


@dataclass
class LightSensorConfig:
    enabled: bool = True
    driver: str = "auto"
    freenove_library_path: Optional[str] = None
    left_channel: int = 0
    right_channel: int = 1


@dataclass
class MobilityConfig:
    enabled: bool = False
    driver: str = "auto"
    freenove_library_path: Optional[str] = None
    max_duty: int = 2200
    default_speed: float = 0.45
    default_duration: float = 0.6
    max_duration: float = 2.0
    command_timeout: float = 2.5
    obstacle_stop_cm: float = 30.0
    require_distance_for_forward: bool = True
    wheel_scale: List[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])


@dataclass
class PowerConfig:
    ups_enabled: bool = False
    ups_model: Optional[str] = None


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
    expression: ExpressionConfig = field(default_factory=ExpressionConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    wake_word: WakeWordConfig = field(default_factory=WakeWordConfig)
    stt: STTConfig = field(default_factory=STTConfig)
    ai: AIConfig = field(default_factory=AIConfig)
    tts: TTSConfig = field(default_factory=TTSConfig)
    freenove: FreenoveConfig = field(default_factory=FreenoveConfig)
    distance: DistanceConfig = field(default_factory=DistanceConfig)
    light_sensors: LightSensorConfig = field(default_factory=LightSensorConfig)
    mobility: MobilityConfig = field(default_factory=MobilityConfig)
    power: PowerConfig = field(default_factory=PowerConfig)
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
        "R08Y_WAKE_WORD_PATH": ("wake_word", "keyword_path"),
        "R08Y_FREENOVE_PATH": ("freenove", "library_path"),
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
        expression=_merge_dict_to_dataclass(ExpressionConfig, data.get("expression")),
        audio=_merge_dict_to_dataclass(AudioConfig, data.get("audio")),
        wake_word=_merge_dict_to_dataclass(WakeWordConfig, data.get("wake_word")),
        stt=_merge_dict_to_dataclass(STTConfig, data.get("stt")),
        ai=_merge_dict_to_dataclass(AIConfig, data.get("ai")),
        tts=_merge_dict_to_dataclass(TTSConfig, data.get("tts")),
        freenove=_merge_dict_to_dataclass(FreenoveConfig, data.get("freenove")),
        distance=_merge_dict_to_dataclass(DistanceConfig, data.get("distance")),
        light_sensors=_merge_dict_to_dataclass(LightSensorConfig, data.get("light_sensors")),
        mobility=_merge_dict_to_dataclass(MobilityConfig, data.get("mobility")),
        power=_merge_dict_to_dataclass(PowerConfig, data.get("power")),
        logging=_merge_dict_to_dataclass(LoggingConfig, data.get("logging")),
    )

    _apply_env_overrides(config)

    # A single Freenove path normally serves every board adapter. Per-device
    # values remain available for unusual installations.
    for section in (config.servos, config.distance, config.light_sensors, config.mobility):
        if not section.freenove_library_path:
            section.freenove_library_path = config.freenove.library_path
    return config


def validate_config(config: R08YConfig) -> List[str]:
    """Return configuration errors without touching physical hardware."""
    errors: List[str] = []
    if config.audio.sample_rate <= 0 or config.audio.channels <= 0:
        errors.append("audio sample_rate and channels must be positive")
    if (
        config.audio.hardware_sample_rate is not None
        and config.audio.hardware_sample_rate <= 0
    ):
        errors.append("audio hardware_sample_rate must be positive or null")
    if config.audio.hardware_channels is not None and config.audio.hardware_channels <= 0:
        errors.append("audio hardware_channels must be positive or null")
    if config.audio.capture_backend not in {"auto", "alsa", "pyaudio"}:
        errors.append("audio capture_backend must be auto, alsa, or pyaudio")
    if config.wake_word.engine not in {"porcupine", "rms"}:
        errors.append("wake_word engine must be porcupine or rms")
    if config.face_detection.method not in {"haar", "dnn"}:
        errors.append("face_detection method must be haar or dnn")
    if not config.wake_word.phrase.strip():
        errors.append("wake_word phrase must not be empty")
    if not 0 <= config.wake_word.sensitivity <= 1:
        errors.append("wake_word sensitivity must be between 0 and 1")
    if (
        config.wake_word.engine == "porcupine"
        and config.wake_word.keyword_path
        and not config.wake_word.keyword_path.casefold().endswith(".ppn")
    ):
        errors.append("wake_word keyword_path must point to a custom .ppn model")
    if not re.fullmatch(r"[+-]\d+%", config.tts.rate):
        errors.append("tts rate must use a signed percentage such as +0% or -10%")
    if not re.fullmatch(r"[+-]\d+Hz", config.tts.pitch):
        errors.append("tts pitch must use signed Hz such as +0Hz or -10Hz")
    if not re.fullmatch(r"[+-]\d+%", config.tts.volume):
        errors.append("tts volume must use a signed percentage such as +0% or -10%")
    if config.servos.driver == "gpio" and (
        config.servos.gpio_pan_pin is None or config.servos.gpio_tilt_pin is None
    ):
        errors.append("GPIO servo mode requires gpio_pan_pin and gpio_tilt_pin")
    if config.expression.update_interval <= 0:
        errors.append("expression update_interval must be positive")
    if config.expression.max_step_degrees <= 0:
        errors.append("expression max_step_degrees must be positive")
    if not 0 < config.expression.gesture_scale <= 1.5:
        errors.append("expression gesture_scale must be greater than 0 and no more than 1.5")
    if any(
        value < 0 or value > 10
        for value in (
            config.expression.idle_sway_degrees,
            config.expression.thinking_sway_degrees,
            config.expression.speaking_bob_degrees,
        )
    ):
        errors.append("expression ambient movement must be between 0 and 10 degrees")
    if not 1 <= config.expression.queue_size <= 32:
        errors.append("expression queue_size must be between 1 and 32")
    if config.distance.driver == "gpio" and (
        config.distance.trigger_pin is None or config.distance.echo_pin is None
    ):
        errors.append("GPIO distance mode requires explicit trigger_pin and echo_pin")
    if not 1 <= config.mobility.max_duty <= 4095:
        errors.append("mobility max_duty must be between 1 and 4095")
    if not 0 < config.mobility.default_speed <= 1:
        errors.append("mobility default_speed must be between 0 and 1")
    if not 0 < config.mobility.default_duration <= config.mobility.max_duration:
        errors.append("mobility default_duration must be positive and no greater than max_duration")
    if config.mobility.command_timeout <= config.mobility.max_duration:
        errors.append("mobility command_timeout must be greater than max_duration")
    if (
        len(config.mobility.wheel_scale) != 4
        or any(scale == 0 or abs(scale) > 1 for scale in config.mobility.wheel_scale)
    ):
        errors.append(
            "mobility wheel_scale must contain four non-zero values between -1 and 1"
        )
    return errors
