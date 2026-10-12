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
    assert config.stt.provider == "huggingface"
    assert config.tts.playback_command == "pw-play"
    assert config.tts.provider == "huggingface"
    assert config.tts.huggingface_voice == "Aiden"
    assert config.tts.voice == "en-US-AndrewMultilingualNeural"
    assert config.tts.rate == "+1%"
    assert config.tts.pitch == "+2Hz"
    assert "Curious, observant" in config.ai.personality_profile
    assert "lightly playful droid personality" in config.ai.spoken_style
    assert config.expression.enabled is True
    assert config.expression.max_step_degrees == 3.0
    assert config.interaction.acknowledgement_cue is True
    assert config.interaction.response_gestures is True
    assert config.interaction.cue_gain == 0.14
    assert config.tracker.patrol_enabled is False
    assert config.wake_word.phrase == "ROBY"
    assert config.wake_word.keyword_path == "/opt/r08y/wake_words/roby.ppn"
    assert config.wake_word.allow_rms_fallback is False
    assert config.mobility.require_distance_for_forward is True


def test_shared_freenove_path_propagates(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("freenove:\n  library_path: /example/freenove\n")
    config = load_config(str(config_file))
    assert config.servos.freenove_library_path == "/example/freenove"
    assert config.distance.freenove_library_path == "/example/freenove"
    assert config.mobility.freenove_library_path == "/example/freenove"


def test_existing_config_inherits_spoken_style_when_field_is_absent(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("ai:\n  system_prompt: You are R08Y.\n")

    config = load_config(str(config_file))

    assert config.ai.system_prompt == "You are R08Y."
    assert "Curious, observant" in config.ai.personality_profile
    assert "natural contractions" in config.ai.spoken_style


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


def test_invalid_tts_provider_and_timeout_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "tts:\n"
        "  provider: unknown\n"
        "  huggingface_timeout_seconds: 0\n"
    )

    errors = validate_config(load_config(str(config_file)))

    assert any("tts provider" in error for error in errors)
    assert any("huggingface_timeout_seconds" in error for error in errors)


def test_invalid_stt_provider_and_huggingface_settings_are_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "stt:\n"
        "  provider: unknown\n"
        "  huggingface_session_url: http://example.invalid/session\n"
        "  huggingface_timeout_seconds: 0\n"
    )

    errors = validate_config(load_config(str(config_file)))

    assert any("stt provider" in error for error in errors)
    assert any("stt huggingface_session_url" in error for error in errors)
    assert any("stt huggingface_timeout_seconds" in error for error in errors)


def test_huggingface_token_comes_from_environment(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("tts:\n  provider: huggingface\n")
    monkeypatch.setenv("R08Y_HF_TOKEN", "hf-environment")

    config = load_config(str(config_file))

    assert config.stt.huggingface_token == "hf-environment"
    assert config.tts.huggingface_token == "hf-environment"


def test_invalid_expression_motion_is_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "expression:\n"
        "  update_interval: 0\n"
        "  max_step_degrees: -1\n"
        "  gesture_scale: 2\n"
        "  idle_sway_degrees: 11\n"
        "  queue_size: 0\n"
    )
    errors = validate_config(load_config(str(config_file)))
    assert any("update_interval" in error for error in errors)
    assert any("max_step_degrees" in error for error in errors)
    assert any("gesture_scale" in error for error in errors)
    assert any("ambient movement" in error for error in errors)
    assert any("queue_size" in error for error in errors)


def test_invalid_interaction_cue_gain_is_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("interaction:\n  cue_gain: 0.75\n")

    errors = validate_config(load_config(str(config_file)))

    assert any("cue_gain" in error for error in errors)


def test_invalid_wake_word_configuration_is_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "wake_word:\n"
        "  engine: guess\n"
        "  phrase: ''\n"
        "  keyword_path: model.txt\n"
        "  sensitivity: 2\n"
    )
    errors = validate_config(load_config(str(config_file)))
    assert any("engine" in error for error in errors)
    assert any("phrase" in error for error in errors)
    assert any("sensitivity" in error for error in errors)


def test_preserved_null_wake_model_does_not_block_service_start(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "wake_word:\n"
        "  engine: porcupine\n"
        "  phrase: ROBY\n"
        "  keyword_path: null\n"
        "  sensitivity: 0.5\n"
    )

    errors = validate_config(load_config(str(config_file)))

    assert not any("keyword_path" in error for error in errors)
