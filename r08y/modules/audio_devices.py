"""Logical audio-device discovery for PortAudio and PipeWire."""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass
from typing import Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    kind: str


def name_matches(candidate: str, requested: str) -> bool:
    return requested.casefold() in candidate.casefold()


def find_portaudio_input(
    pa,
    requested_name: str = "",
    requested_index: Optional[int] = None,
    require_match: bool = False,
):
    """Return ``(index, info)`` for the requested input device."""
    if requested_index is not None:
        info = pa.get_device_info_by_index(requested_index)
        if int(info.get("maxInputChannels", 0)) <= 0:
            raise ValueError(f"Audio device {requested_index} has no input channels")
        return requested_index, info

    inputs = []
    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        if int(info.get("maxInputChannels", 0)) > 0:
            inputs.append((index, info))
            if requested_name and name_matches(str(info.get("name", "")), requested_name):
                return int(info.get("index", index)), info

    if requested_name and require_match:
        names = ", ".join(str(info.get("name", index)) for index, info in inputs) or "none"
        raise LookupError(f"Input device matching '{requested_name}' not found; available: {names}")

    if inputs:
        default = pa.get_default_input_device_info()
        default_index = int(default["index"])
        return default_index, default
    raise LookupError("No audio input devices are available")


def parse_wpctl_status(output: str) -> List[AudioDevice]:
    """Parse named PipeWire sinks and sources from ``wpctl status -n``."""
    devices: List[AudioDevice] = []
    section: Optional[str] = None
    entry_pattern = re.compile(r"(?:^|\s)(?:\*\s*)?(\d+)\.\s+(.+?)(?:\s+\[vol:.*)?$")

    for raw_line in output.splitlines():
        line = raw_line.strip()
        if "Sinks:" in line:
            section = "sink"
            continue
        if "Sources:" in line:
            section = "source"
            continue
        if any(label in line for label in ("Devices:", "Filters:", "Streams:", "Video")):
            section = None
            continue
        if section is None:
            continue
        match = entry_pattern.search(line)
        if match:
            devices.append(AudioDevice(id=match.group(1), name=match.group(2).strip(), kind=section))
    return devices


class PipeWireResolver:
    """Resolve stable friendly names to volatile PipeWire node IDs."""

    def __init__(self, runner=subprocess.run):
        self._runner = runner

    @property
    def available(self) -> bool:
        return shutil.which("wpctl") is not None

    def list_devices(self) -> List[AudioDevice]:
        if not self.available:
            return []
        try:
            result = self._runner(
                ["wpctl", "status", "-n"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("wpctl status failed: %s", result.stderr.strip())
                return []
            return parse_wpctl_status(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("Unable to query PipeWire devices")
            return []

    def resolve(self, kind: str, requested_name: str) -> Optional[AudioDevice]:
        for device in self.list_devices():
            if device.kind == kind and name_matches(device.name, requested_name):
                return device
        return None

    def set_default(self, device: AudioDevice) -> bool:
        if not self.available:
            return False
        try:
            result = self._runner(
                ["wpctl", "set-default", device.id],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("Could not set PipeWire default %s: %s", device.kind, result.stderr.strip())
                return False
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False

    def configure_defaults(self, requested_name: str) -> dict[str, Optional[AudioDevice]]:
        selected = {
            "sink": self.resolve("sink", requested_name),
            "source": self.resolve("source", requested_name),
        }
        for device in selected.values():
            if device is not None:
                self.set_default(device)
        return selected


def format_device_names(devices: Iterable[AudioDevice]) -> str:
    return ", ".join(f"{device.kind}:{device.name}" for device in devices)
