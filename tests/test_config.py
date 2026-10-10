from pathlib import Path

from r08y.config import load_config, validate_config


def test_example_config_is_valid():
    path = Path(__file__).parents[1] / "config.example.yaml"
    config = load_config(str(path))
    assert validate_config(config) == []
    assert config.servos.driver == "freenove"
    assert config.audio.device_name == "XVF3800"
    assert config.audio.hardware_sample_rate == 16000
    assert config.audio.hardware_channels == 2
    assert config.audio.capture_backend == "auto"
    assert config.tts.playback_command == "pw-play"
    assert config.tts.pitch == "+0Hz"
    assert config.mobility.require_distance_for_forward is True


def test_shared_freenove_path_propagates(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("freenove:\n  library_path: /example/freenove\n")
    config = load_config(str(config_file))
    assert config.servos.freenove_library_path == "/example/freenove"
    assert config.distance.freenove_library_path == "/example/freenove"
    assert config.mobility.freenove_library_path == "/example/freenove"


def test_invalid_gpio_placeholders_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("servos:\n  driver: gpio\n")
    errors = validate_config(load_config(str(config_file)))
    assert any("gpio_pan_pin" in error for error in errors)


def test_mobility_safety_timing_and_wheel_reversal_are_validated(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "mobility:\n"
        "  max_duration: 2.0\n"
        "  command_timeout: 2.0\n"
        "  wheel_scale: [1.0, -1.0, 0.0, 1.1]\n"
    )
    errors = validate_config(load_config(str(config_file)))
    assert any("command_timeout" in error for error in errors)
    assert any("wheel_scale" in error for error in errors)

    config_file.write_text(
        "mobility:\n"
        "  max_duration: 2.0\n"
        "  command_timeout: 2.5\n"
        "  wheel_scale: [1.0, -1.0, 0.9, -0.9]\n"
    )
    assert validate_config(load_config(str(config_file))) == []


def test_invalid_native_audio_format_is_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "audio:\n"
        "  hardware_sample_rate: 0\n"
        "  hardware_channels: -1\n"
    )
    errors = validate_config(load_config(str(config_file)))
    assert any("hardware_sample_rate" in error for error in errors)
    assert any("hardware_channels" in error for error in errors)


def test_invalid_audio_capture_backend_is_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("audio:\n  capture_backend: blocking-library\n")
    errors = validate_config(load_config(str(config_file)))
    assert any("capture_backend" in error for error in errors)


def test_invalid_tts_adjustments_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "tts:\n"
        "  rate: fast\n"
        "  pitch: deep\n"
        "  volume: loud\n"
    )
    errors = validate_config(load_config(str(config_file)))
    assert any("tts rate" in error for error in errors)
    assert any("tts pitch" in error for error in errors)
    assert any("tts volume" in error for error in errors)
