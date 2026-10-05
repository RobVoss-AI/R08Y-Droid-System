from r08y.modules.audio_devices import find_portaudio_input, parse_wpctl_status


WPCTL_STATUS = """
Audio
 ├─ Sinks:
 │  *   89. reSpeaker XVF3800 4-Mic Array Analog Stereo [vol: 0.80]
 ├─ Sources:
 │  *   90. reSpeaker XVF3800 4-Mic Array Analog Stereo [vol: 1.00]
 └─ Streams:
"""


def test_parse_wpctl_status_finds_named_sink_and_source():
    devices = parse_wpctl_status(WPCTL_STATUS)
    assert [(device.id, device.kind) for device in devices] == [("89", "sink"), ("90", "source")]
    assert all("XVF3800" in device.name for device in devices)


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


def test_portaudio_selection_does_not_silently_fallback():
    try:
        find_portaudio_input(FakePyAudio(), requested_name="missing", require_match=True)
    except LookupError as exc:
        assert "missing" in str(exc)
    else:
        raise AssertionError("Expected named-device lookup to fail")
