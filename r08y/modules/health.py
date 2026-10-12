"""Non-actuating health checks for startup and diagnostics."""

from __future__ import annotations

import asyncio
import socket
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, Optional

from r08y.config import R08YConfig
from r08y.hardware.freenove import discover_freenove_path


@dataclass(frozen=True)
class ComponentHealth:
    status: str
    detail: str


@dataclass
class HealthReport:
    components: Dict[str, ComponentHealth] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return all(item.status != "fail" for item in self.components.values())

    def as_dict(self) -> dict:
        return {name: asdict(value) for name, value in self.components.items()}

    def summary(self) -> str:
        return ", ".join(
            f"{name}={item.status}" for name, item in self.components.items()
        )


def _network_available() -> bool:
    try:
        socket.getaddrinfo("api.anthropic.com", 443)
        return True
    except OSError:
        return False


def _read_throttle() -> ComponentHealth:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        value = result.stdout.strip()
        if result.returncode == 0 and value == "throttled=0x0":
            return ComponentHealth("pass", value)
        if result.returncode == 0:
            return ComponentHealth("warn", value or "throttle state unavailable")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return ComponentHealth("skip", "vcgencmd unavailable on this system")


def _platform_detail() -> str:
    model_path = Path("/proc/device-tree/model")
    if model_path.exists():
        try:
            return model_path.read_text(errors="ignore").strip("\x00\n")
        except OSError:
            pass
    return "non-Raspberry Pi development system"


async def collect_runtime_health(
    config: R08YConfig,
    *,
    camera,
    servo,
    voice_input,
    tts,
    distance,
    light_sensors,
    mobility,
    face_detector=None,
) -> HealthReport:
    """Describe initialized components and sample passive sensors once."""
    report = HealthReport()
    report.components["platform"] = ComponentHealth("pass", _platform_detail())

    freenove_path = discover_freenove_path(config.freenove.library_path)
    report.components["freenove_library"] = ComponentHealth(
        "pass" if freenove_path else "fail",
        str(freenove_path) if freenove_path else "official Code/Server path not found",
    )
    report.components["camera"] = ComponentHealth(
        "pass" if camera.available else ("skip" if not config.camera.enabled else "fail"),
        f"backend={camera.backend}" if camera.available else "not available",
    )
    if face_detector is not None:
        report.components["face_detection"] = ComponentHealth(
            "pass" if getattr(face_detector, "available", False) else "warn",
            getattr(face_detector, "detail", type(face_detector).__name__),
        )
    report.components["pan_tilt"] = ComponentHealth(
        "pass" if servo.available else ("skip" if not config.servos.enabled else "fail"),
        type(servo).__name__,
    )
    report.components["microphone"] = ComponentHealth(
        "pass" if voice_input.available else "fail",
        getattr(
            voice_input,
            "capture_detail",
            voice_input.device_name or "not available",
        ),
    )
    wake_status = getattr(voice_input, "wake_status", "fail")
    if wake_status not in {"pass", "warn", "fail"}:
        wake_status = "fail"
    report.components["wake_word"] = ComponentHealth(
        wake_status,
        getattr(voice_input, "wake_detail", "wake-word status unavailable"),
    )
    report.components["speaker_path"] = ComponentHealth(
        "pass" if tts.playback_available else "fail",
        getattr(
            tts,
            "output_detail",
            config.tts.output_device_name or "system default",
        ),
    )

    distance_value: Optional[float] = None
    if distance.available:
        distance_value = await distance.read_cm()
    report.components["ultrasonic"] = ComponentHealth(
        "pass" if distance_value is not None else ("skip" if not config.distance.enabled else "fail"),
        f"{distance_value:.1f} cm" if distance_value is not None else "no valid reading",
    )

    light_reading = await light_sensors.read() if light_sensors.available else None
    report.components["light_sensors"] = ComponentHealth(
        "pass" if light_reading else ("skip" if not config.light_sensors.enabled else "fail"),
        (
            f"left={light_reading.left_volts:.2f}V right={light_reading.right_volts:.2f}V"
            if light_reading
            else "no valid reading"
        ),
    )
    report.components["mobility"] = ComponentHealth(
        "pass" if mobility.available else ("skip" if not config.mobility.enabled else "fail"),
        f"{type(mobility).__name__}; wheels not actuated",
    )
    network_ok = await asyncio.to_thread(_network_available)
    report.components["network"] = ComponentHealth(
        "pass" if network_ok else "warn",
        "DNS available" if network_ok else "network not confirmed",
    )
    report.components["power"] = await asyncio.to_thread(_read_throttle)
    report.components["ups"] = ComponentHealth(
        "skip" if not config.power.ups_enabled else "warn",
        "not integrated" if not config.power.ups_enabled else (config.power.ups_model or "configured without monitor"),
    )
    return report
