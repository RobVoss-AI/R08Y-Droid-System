"""Logical audio-device discovery and bounded ALSA capture."""

from __future__ import annotations

import logging
import os
import re
import select
import shutil
import subprocess
import time
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AudioDevice:
    id: str
    name: str
    kind: str


@dataclass(frozen=True)
class AlsaCaptureDevice:
    """An ALSA capture endpoint discovered from ``arecord -l``."""

    card: int
    device: int
    card_id: str
    card_name: str
    device_name: str

    @property
    def hardware_id(self) -> str:
        return f"hw:{self.card},{self.device}"

    @property
    def display_name(self) -> str:
        return f"{self.card_name} {self.device_name}".strip()


def name_matches(candidate: str, requested: str) -> bool:
    return requested.casefold() in candidate.casefold()


def find_portaudio_input(
    pa,
    requested_name: str = "",
    requested_index: Optional[int] = None,
    require_match: bool = False,
    requested_aliases: Iterable[str] = (),
):
    """Return ``(index, info)`` for the requested input device."""
    if requested_index is not None:
        info = pa.get_device_info_by_index(requested_index)
        if int(info.get("maxInputChannels", 0)) <= 0:
            raise ValueError(f"Audio device {requested_index} has no input channels")
        return requested_index, info

    requested_names = [name for name in (requested_name, *requested_aliases) if name]
    inputs = []
    for index in range(pa.get_device_count()):
        info = pa.get_device_info_by_index(index)
        if int(info.get("maxInputChannels", 0)) > 0:
            inputs.append((index, info))
            if any(
                name_matches(str(info.get("name", "")), name)
                for name in requested_names
            ):
                return int(info.get("index", index)), info

    if requested_names and require_match:
        names = ", ".join(str(info.get("name", index)) for index, info in inputs) or "none"
        raise LookupError(f"Input device matching '{requested_name}' not found; available: {names}")

    if inputs:
        default = pa.get_default_input_device_info()
        default_index = int(default["index"])
        return default_index, default
    raise LookupError("No audio input devices are available")


def parse_arecord_list(output: str) -> List[AlsaCaptureDevice]:
    """Parse ALSA capture hardware without assuming a stable card number."""
    devices: List[AlsaCaptureDevice] = []
    pattern = re.compile(
        r"^card\s+(\d+):\s+([^\s]+)\s+\[(.*?)\],\s+"
        r"device\s+(\d+):\s+.*?\[(.*?)\]\s*$",
        re.IGNORECASE,
    )
    for raw_line in output.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        devices.append(
            AlsaCaptureDevice(
                card=int(match.group(1)),
                device=int(match.group(4)),
                card_id=match.group(2).strip(),
                card_name=match.group(3).strip(),
                device_name=match.group(5).strip(),
            )
        )
    return devices


def find_alsa_capture(
    devices: Iterable[AlsaCaptureDevice], requested_name: str
) -> Optional[AlsaCaptureDevice]:
    """Find an ALSA endpoint by its stable card or product name."""
    for device in devices:
        searchable = " ".join(
            (device.card_id, device.card_name, device.device_name)
        )
        if name_matches(searchable, requested_name):
            return device
    return None


class AlsaResolver:
    """Resolve capture hardware by name while treating card numbers as volatile."""

    def __init__(self, runner=subprocess.run):
        self._runner = runner

    @property
    def available(self) -> bool:
        return shutil.which("arecord") is not None

    def list_capture_devices(self) -> List[AlsaCaptureDevice]:
        if not self.available:
            return []
        try:
            result = self._runner(
                ["arecord", "-l"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if result.returncode != 0:
                logger.warning("arecord -l failed: %s", result.stderr.strip())
                return []
            return parse_arecord_list(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("Unable to query ALSA capture devices")
            return []

    def resolve(self, requested_name: str) -> Optional[AlsaCaptureDevice]:
        return find_alsa_capture(self.list_capture_devices(), requested_name)


class AlsaCaptureStream:
    """Read raw PCM from ``arecord`` with a deadline and stoppable process."""

    def __init__(
        self,
        hardware_id: str,
        sample_rate: int,
        channels: int,
        *,
        read_timeout: float = 2.0,
        process_factory: Callable = subprocess.Popen,
        selector: Callable = select.select,
        reader: Callable = os.read,
        clock: Callable = time.monotonic,
    ):
        self.hardware_id = hardware_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.read_timeout = read_timeout
        self._selector = selector
        self._reader = reader
        self._clock = clock
        command = [
            "arecord",
            "-q",
            "-D",
            hardware_id,
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            str(channels),
            "-t",
            "raw",
        ]
        self._process = process_factory(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if self._process.stdout is None:
            self.stop_stream()
            raise OSError("arecord did not provide an audio stream")

    def is_active(self) -> bool:
        return self._process.poll() is None

    def _exit_detail(self) -> str:
        if self._process.poll() is None:
            try:
                self._process.wait(timeout=0.1)
            except subprocess.TimeoutExpired:
                return "arecord stopped before returning audio"
        if self._process.stderr is None:
            return "arecord stopped before returning audio"
        detail = self._process.stderr.read().decode(errors="replace").strip()
        return detail or f"arecord exited with status {self._process.returncode}"

    def read(self, frames: int, exception_on_overflow: bool = False) -> bytes:
        del exception_on_overflow
        expected = frames * self.channels * 2
        captured = bytearray()
        deadline = self._clock() + self.read_timeout
        descriptor = self._process.stdout.fileno()

        while len(captured) < expected:
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TimeoutError(
                    f"ALSA capture timed out after {self.read_timeout:.1f}s without a full audio chunk"
                )
            ready, _, _ = self._selector([descriptor], [], [], remaining)
            if not ready:
                raise TimeoutError(
                    f"ALSA capture timed out after {self.read_timeout:.1f}s without audio data"
                )
            chunk = self._reader(descriptor, expected - len(captured))
            if not chunk:
                raise OSError(self._exit_detail())
            captured.extend(chunk)
        return bytes(captured)

    def stop_stream(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=1)

    def close(self) -> None:
        self.stop_stream()
        for pipe in (self._process.stdout, self._process.stderr):
            if pipe is not None:
                pipe.close()


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
