import io
import subprocess

from r08y.modules.audio_devices import (
    AlsaCaptureStream,
    find_alsa_capture,
    find_portaudio_input,
    parse_arecord_list,
    parse_wpctl_status,
)


WPCTL_STATUS = """
Audio
 ├─ Sinks:
 │  *   89. reSpeaker XVF3800 4-Mic Array Analog Stereo [vol: 0.80]
 ├─ Sources:
 │  *   90. reSpeaker XVF3800 4-Mic Array Analog Stereo [vol: 1.00]
 └─ Streams:
"""

ARECORD_LIST = """
**** List of CAPTURE Hardware Devices ****
card 2: Webcam [USB Webcam], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
card 3: XVF3800 [reSpeaker XVF3800 4-Mic Array], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""


def test_parse_wpctl_status_finds_named_sink_and_source():
    devices = parse_wpctl_status(WPCTL_STATUS)
    assert [(device.id, device.kind) for device in devices] == [("89", "sink"), ("90", "source")]
    assert all("XVF3800" in device.name for device in devices)


def test_parse_arecord_list_resolves_xvf3800_without_fixed_card_number():
    devices = parse_arecord_list(ARECORD_LIST)
    selected = find_alsa_capture(devices, "XVF3800")
    assert selected is not None
    assert selected.card == 3
    assert selected.device == 0
    assert selected.hardware_id == "hw:3,0"


def test_alsa_capture_name_lookup_preserves_other_usb_mics():
    selected = find_alsa_capture(parse_arecord_list(ARECORD_LIST), "Webcam")
    assert selected is not None
    assert selected.hardware_id == "hw:2,0"


class FakePipe:
    def __init__(self, data=b""):
        self.data = io.BytesIO(data)
        self.closed = False

    def fileno(self):
        return 42

    def read(self):
        return self.data.read()

    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self):
        self.stdout = FakePipe()
        self.stderr = FakePipe()
        self.returncode = None
        self.terminated = False

    def poll(self):
        return self.returncode

    def terminate(self):
        self.terminated = True
        self.returncode = 0

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


def test_alsa_stream_builds_native_command_and_reads_one_chunk():
    process = FakeProcess()
    calls = []

    def process_factory(command, **kwargs):
        calls.append((command, kwargs))
        return process

    stream = AlsaCaptureStream(
        "hw:7,0",
        16000,
        2,
        process_factory=process_factory,
        selector=lambda readable, _writable, _errors, _timeout: (readable, [], []),
        reader=lambda _descriptor, size: b"\x01" * size,
        clock=lambda: 0.0,
    )
    assert stream.read(512) == b"\x01" * 2048
    assert calls[0][0] == [
        "arecord", "-q", "-D", "hw:7,0", "-f", "S16_LE",
        "-r", "16000", "-c", "2", "-t", "raw",
    ]
    assert calls[0][1]["stdout"] == subprocess.PIPE
    stream.close()
    assert process.terminated
    assert process.stdout.closed


def test_alsa_stream_has_a_hard_read_timeout():
    process = FakeProcess()
    stream = AlsaCaptureStream(
        "hw:3,0",
        16000,
        2,
        read_timeout=0.25,
        process_factory=lambda _command, **_kwargs: process,
        selector=lambda _readable, _writable, _errors, _timeout: ([], [], []),
        clock=lambda: 0.0,
    )
    try:
        stream.read(512)
    except TimeoutError as exc:
        assert "timed out" in str(exc)
    else:
        raise AssertionError("Expected bounded ALSA read to time out")
    stream.close()


class FakePyAudio:
    def __init__(self):
        self.devices = [
            {"index": 0, "name": "Built-in", "maxInputChannels": 1},
            {"index": 3, "name": "reSpeaker XVF3800 4-Mic Array", "maxInputChannels": 2},
        ]

    def get_device_count(self):
        return len(self.devices)

    def get_device_info_by_index(self, index):
        return self.devices[index]

    def get_default_input_device_info(self):
        return self.devices[0]


def test_portaudio_selection_prefers_logical_name():
    index, info = find_portaudio_input(FakePyAudio(), requested_name="XVF3800", require_match=True)
    assert index == 3
    assert info["index"] == 3


def test_portaudio_selection_accepts_name_discovered_alsa_alias():
    fake = FakePyAudio()
    fake.devices[1]["name"] = "USB Audio (hw:7,0)"
    index, _ = find_portaudio_input(
        fake,
        requested_name="XVF3800",
        requested_aliases=["hw:7,0"],
        require_match=True,
    )
    assert index == 3


def test_portaudio_selection_does_not_silently_fallback():
    try:
        find_portaudio_input(FakePyAudio(), requested_name="missing", require_match=True)
    except LookupError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Expected named-device lookup to fail")
