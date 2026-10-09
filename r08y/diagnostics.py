"""Command-line hardware smoke tests for the Raspberry Pi deployment."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass
from typing import List

from r08y.config import R08YConfig, validate_config
from r08y.hardware.freenove import FreenoveLibrary, discover_freenove_path
from r08y.modules.audio_devices import (
    AlsaResolver,
    PipeWireResolver,
    find_alsa_capture,
    format_device_names,
    name_matches,
)


@dataclass(frozen=True)
class SmokeResult:
    name: str
    status: str
    detail: str


def _result(name: str, passed: bool, detail: str) -> SmokeResult:
    return SmokeResult(name, "pass" if passed else "fail", detail)


def _run_command(args: list[str], timeout: float = 5) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)


def _passive_checks(config: R08YConfig) -> List[SmokeResult]:
    results: List[SmokeResult] = []
    errors = validate_config(config)
    results.append(_result("configuration", not errors, "; ".join(errors) or "valid"))

    path = discover_freenove_path(config.freenove.library_path)
    if path:
        library = FreenoveLibrary(str(path))
        modules = library.module_status()
        results.append(_result("freenove_library", all(modules.values()), f"{path}: {modules}"))
        if config.light_sensors.enabled:
            try:
                params_path, pcb_version = library.validate_parameter_file()
                results.append(
                    _result(
                        "freenove_parameters",
                        True,
                        f"{params_path}: PCB version {pcb_version}",
                    )
                )
            except (FileNotFoundError, ValueError) as exc:
                results.append(_result("freenove_parameters", False, str(exc)))
    else:
        results.append(_result("freenove_library", False, "official Code/Server directory not found"))

    if shutil.which("i2cdetect"):
        try:
            scan = _run_command(["i2cdetect", "-y", "1"])
            found_pwm = "40" in scan.stdout
            found_adc = "48" in scan.stdout
            results.append(
                _result(
                    "i2c",
                    scan.returncode == 0 and found_pwm and found_adc,
                    f"PCA9685@0x40={'yes' if found_pwm else 'no'}, ADS7830@0x48={'yes' if found_adc else 'no'}",
                )
            )
        except subprocess.TimeoutExpired:
            results.append(_result("i2c", False, "i2cdetect timed out"))
    else:
        results.append(SmokeResult("i2c", "skip", "i2cdetect is not installed"))

    alsa = AlsaResolver()
    if alsa.available:
        alsa_devices = alsa.list_capture_devices()
        alsa_capture = find_alsa_capture(alsa_devices, config.audio.device_name)
        used_fallback = False
        if alsa_capture is None and not config.audio.require_device_match and alsa_devices:
            alsa_capture = alsa_devices[0]
            used_fallback = True
        results.append(
            _result(
                "audio_alsa_capture",
                alsa_capture is not None,
                (
                    f"{alsa_capture.display_name} ({alsa_capture.hardware_id}; "
                    f"{'default fallback' if used_fallback else 'discovered by name'})"
                    if alsa_capture
                    else (
                        f"no capture device matching '{config.audio.device_name}'; "
                        "available: "
                        + (", ".join(device.display_name for device in alsa_devices) or "none")
                    )
                ),
            )
        )
    else:
        results.append(SmokeResult("audio_alsa_capture", "skip", "arecord is not installed"))

    resolver = PipeWireResolver()
    devices = resolver.list_devices()
    if resolver.available:
        sources = [device for device in devices if device.kind == "source"]
        sinks = [device for device in devices if device.kind == "sink"]
        source = next(
            (
                device for device in sources
                if name_matches(device.name, config.audio.device_name)
            ),
            None,
        )
        sink = next(
            (
                device for device in sinks
                if name_matches(device.name, config.tts.output_device_name)
            ),
            None,
        )
        if source is None and not config.audio.require_device_match and sources:
            source = sources[0]
        if sink is None and not config.tts.require_output_device_match and sinks:
            sink = sinks[0]
        selected_audio = [device for device in (sink, source) if device is not None]
        missing = []
        if source is None:
            missing.append(f"source '{config.audio.device_name}'")
        if sink is None:
            missing.append(f"sink '{config.tts.output_device_name}'")
        detail = format_device_names(selected_audio) or "no usable sink/source"
        if missing:
            detail = f"{detail}; missing " + ", ".join(missing)
        results.append(
            _result(
                "audio_pipewire",
                source is not None and sink is not None,
                detail,
            )
        )
    else:
        results.append(SmokeResult("audio_pipewire", "skip", "wpctl is not installed"))

    if shutil.which("vcgencmd"):
        throttle = _run_command(["vcgencmd", "get_throttled"], timeout=3)
        value = throttle.stdout.strip()
        results.append(_result("power", value == "throttled=0x0", value or "no response"))
    else:
        results.append(SmokeResult("power", "skip", "vcgencmd is unavailable"))

    results.append(
        SmokeResult(
            "ups",
            "skip" if not config.power.ups_enabled else "warn",
            "not integrated" if not config.power.ups_enabled else "configured; live telemetry not implemented",
        )
    )
    return results


async def _exercise_hardware(config: R08YConfig, exercise_mobility: bool) -> List[SmokeResult]:
    # Imports are deferred so passive diagnostics can still explain missing Pi packages.
    from r08y.modules.camera import CameraCapture
    from r08y.modules.mobility import create_mobility_controller
    from r08y.modules.sensors import create_distance_sensor, create_light_sensors
    from r08y.modules.servo_controller import create_servo_controller
    from r08y.modules.text_to_speech import TextToSpeech
    from r08y.modules.voice_input import VoiceInput
    from r08y.skills.sound_effects import _generate_wav

    results: List[SmokeResult] = []
    camera = CameraCapture(config.camera)
    servo = create_servo_controller(config.servos)
    distance = create_distance_sensor(config.distance)
    light_sensors = create_light_sensors(config.light_sensors)
    voice = VoiceInput(config.audio, config.wake_word)
    tts = TextToSpeech(config.tts)
    mobility = create_mobility_controller(config.mobility, distance)

    try:
        await light_sensors.start()
        await asyncio.gather(
            camera.start(), servo.start(), distance.start(), voice.start(), tts.start()
        )
        await asyncio.sleep(0.8)
        frame = await camera.get_frame()
        results.append(
            _result(
                "camera_frame",
                frame is not None,
                f"backend={camera.backend}, shape={getattr(frame, 'shape', None)}",
            )
        )
        results.append(_result("pan_tilt_home", servo.available, type(servo).__name__))

        distance_value = await distance.read_cm()
        results.append(
            _result(
                "ultrasonic_read",
                distance_value is not None,
                f"{distance_value:.1f} cm" if distance_value is not None else "no reading",
            )
        )
        light = await light_sensors.read()
        results.append(
            _result(
                "light_sensor_read",
                light is not None,
                f"left={light.left_volts:.2f}V right={light.right_volts:.2f}V" if light else "no reading",
            )
        )
        level = await voice.measure_level()
        level_ok = level is not None and level > 0
        results.append(
            _result(
                "microphone_capture",
                level_ok,
                (
                    f"{voice.capture_detail}; average RMS={level:.1f}"
                    if level is not None
                    else voice.capture_detail
                ),
            )
        )
        played = await tts.play_wav_bytes(_generate_wav(660, 0.2))
        results.append(
            _result(
                "speaker_command",
                played,
                "test tone command completed; confirm sound was audible",
            )
        )

        if exercise_mobility:
            await mobility.start()
            if mobility.available:
                sequence = (
                    (0.0, 0.2, 0.0),
                    (0.0, -0.2, 0.0),
                    (0.0, 0.0, 0.2),
                    (0.0, 0.0, -0.2),
                    (0.2, 0.0, 0.0),
                    (-0.2, 0.0, 0.0),
                )
                for forward, strafe, turn in sequence:
                    await mobility.move_for(forward, strafe, turn, 0.25)
                    await asyncio.sleep(0.15)
                results.append(_result("mobility_exercise", True, "six low-speed movements completed"))
            else:
                results.append(_result("mobility_exercise", False, "motor adapter unavailable"))
        else:
            results.append(SmokeResult("mobility_exercise", "skip", "requires explicit wheels-raised confirmation"))
    except Exception as exc:
        results.append(SmokeResult("exercise", "fail", f"{type(exc).__name__}: {exc}"))
    finally:
        await mobility.emergency_stop()
        await asyncio.gather(
            mobility.stop(), camera.stop(), servo.stop(), distance.stop(), light_sensors.stop(),
            voice.stop(), tts.stop(), return_exceptions=True,
        )
    return results


async def run_smoke_test(
    config: R08YConfig,
    *,
    exercise: bool = False,
    exercise_mobility: bool = False,
) -> List[SmokeResult]:
    results = await asyncio.to_thread(_passive_checks, config)
    if exercise:
        results.extend(await _exercise_hardware(config, exercise_mobility))
    return results


def print_smoke_results(results: List[SmokeResult], as_json: bool = False) -> None:
    if as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
        return
    print("R08Y hardware smoke test")
    for result in results:
        print(f"[{result.status.upper():4}] {result.name}: {result.detail}")
