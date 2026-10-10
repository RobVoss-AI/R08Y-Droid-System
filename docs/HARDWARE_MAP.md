# Current Hardware and Architecture Map

This map records how the original R08Y design was adapted to Rob’s working Freenove platform. “Verified” below means verified in code or against the official upstream API. Live hardware behavior still requires the Pi smoke tests.

## Physical hardware

| Hardware | Current state | R08Y decision | Acceptance check |
|---|---|---|---|
| Raspberry Pi 4B, Debian 13 | Working | Reuse. Installer uses Debian packages and a system-site-packages virtual environment. | `vcgencmd get_throttled` returns `0x0`; service starts as `rob`. |
| Freenove 4WD mecanum chassis | Motors working in the existing Freenove environment | Modify. Add an adapter around official `Ordinary_Car.set_motor_model()` and mecanum mixing. No copied mappings. | Wheels-raised six-pattern exercise, followed by guarded floor test. |
| Freenove light sensors | Working | Add an ADC adapter around official `ADC.read_adc(0/1)`. Require valid upstream `params.json`. | Both channels return plausible voltages. |
| Freenove ultrasonic sensor | Working | Replace hardcoded generic HC-SR04 pins with official `Ultrasonic.get_distance()`. | Repeated plausible distance; forward movement blocks inside 30 cm. |
| Freenove camera | Working | Modify capture to try Picamera2, then OpenCV/V4L2, with reconnect and flip settings. | One frame with expected shape; continuous tracking in runtime. |
| Freenove pan/tilt | Working | Use the official `Servo.set_servo_pwm("0"/"1", angle)` adapter behind a single expression controller. | Guided expression sequence and face tracking without binding at configured limits. |
| XVF3800 four-mic USB array | Capture verified at S16_LE, 16 kHz, two channels; LEDs and direction response working | Resolve by name, open the native format, downmix to 16 kHz mono for wake word/STT, and detect the known open-but-broken USB state with a startup read. | Matching ALSA/PipeWire devices, startup capture read, microphone RMS above zero, wake-word turn completes. |
| Seeed 4-ohm 5-watt speaker | Working through the XVF3800 after setting its `PCM 1` level to 100 | Route WAV/TTS through `pw-play` to the PipeWire sink resolved by name. | Audible tone, startup greeting, and AI response. |
| UPS HAT and battery | Not integrated | Defer. Keep disabled and report as skipped. No model-specific code until the exact HAT is identified and tested separately. | Future power and shutdown plan after direct-power acceptance. |

## Reused without architectural change

- `DroidState` as shared context between vision and voice.
- Face detection, face encoding storage, recognition, and PID tracking logic.
- Speech-to-text provider boundary for Groq and OpenAI Whisper.
- Claude conversation layer and SQLite history.
- Event bus, command registry, skill manifests, and auto-discovery model.
- Software-only skills such as weather, timers, dictation, presentations, stories, briefings, and voice profiles.

These modules received only compatibility or lifecycle wiring where required. Their responsibilities remain the same.

## Modified

| Area | Previous assumption | Current behavior |
|---|---|---|
| Camera | USB webcam only | Picamera2 or OpenCV/V4L2, selectable device path, reconnect, flips. |
| Pan/tilt | Generic PCA9685 or guessed GPIO fallback | Official Freenove servo API; GPIO is allowed only with explicit pins. |
| Expressive motion | Face tracking and skills could write servo angles independently | One queue blends tracking, named gestures, and conversation state before clamped servo writes. |
| Distance | Generic HC-SR04 with hardcoded pins in a skill | Shared sensor abstraction using the official Freenove ultrasonic API. |
| Voice input | Fixed numeric audio index and format | Strict device-name selection; bounded XVF3800 `arecord` capture at S16_LE/16 kHz/stereo; downmix/resample; PyAudio fallback; startup read with explicit USB-reseat diagnosis. |
| Speech output | Generic default player/device | Name-resolved XVF3800 PipeWire sink selection with direct `pw-play --target` routing and compatible playback conversion. |
| Main runtime | Vision and voice only | Sensor, mobility, component health, and graceful-stop orchestration. |
| Service | Hardcoded account/path behavior | Installer-rendered user and group, protected environment file, config preflight, signal-aware shutdown. |
| Installer | Generic Pi build with long local dlib compilation | Debian/Pi packages first, system-site packages, optional face recognition, current hardware groups. |
| Skill lifecycle | Failed setup could leave a skill partly registered | Failed setup/start removes commands and pipeline hooks. |

## Added

- `r08y/hardware/freenove.py`: verified upstream module discovery and loading.
- `r08y/modules/sensors.py`: distance and light abstractions with disabled and mock implementations.
- `r08y/modules/mobility.py`: mecanum mixing, wheel calibration, clearance guard, deadman watchdog, emergency stop, and fake controller.
- `r08y/modules/audio_devices.py`: named, timeout-protected ALSA capture plus PortAudio fallback and PipeWire resolution.
- `r08y/modules/health.py`: component-level startup report.
- `r08y/modules/expression_controller.py`: single-owner pan/tilt queue, smoothing, cancellation, and interaction-state body language.
- `r08y/diagnostics.py`: passive discovery and opt-in physical exercises.
- `r08y/skills/mobility_control`: short voice-triggered mecanum movements.
- `r08y/skills/light_sensors`: plain-language sensor readings.
- Tests for config validation, audio resolution, Freenove loading and parameter safety, mecanum mixing and guards, sensors, format conversion, camera lifecycle, servo lifecycle, gesture queueing, cancellation, clamping, and expression diagnostics.

## Explicit unknowns

No code in this repository asserts that the physical wheel polarity, camera device node, or pan/tilt orientation is correct until the Pi reports it. Those differences are isolated in configuration. The XVF3800 capture format below is hardware-verified; other microphones can override it:

- `mobility.wheel_scale`
- `camera.device_path`, `horizontal_flip`, and `vertical_flip`
- servo angle limits and homes
- `audio.hardware_sample_rate` and `hardware_channels`
- `tts.output_target`

ALSA card numbers and PipeWire node IDs remain intentionally undisclosed in configuration. The working Pi reported ALSA card 3/device 0 during bring-up, but both identifiers can change after reboot or USB re-enumeration. R08Y resolves the configured name at each startup.

The exact UPS model and interface remain unknown. R08Y does not pretend to monitor it.

## Safety model

1. Mobility is disabled by default in the Python dataclass and enabled deliberately in the current example configuration.
2. Forward motion fails closed when the distance sensor is absent or invalid.
3. Spoken movement is capped at two seconds.
4. The controller watchdog stops a stale command after 2.5 seconds.
5. Every timed move stops in a `finally` block.
6. Shutdown and smoke-test cleanup send zero duty to every wheel.
7. Motor exercises require an explicit command-line confirmation that the wheels are raised.
8. GPIO fallbacks require explicit pins; no Freenove mapping is guessed.
9. The expression controller clamps every pose to configured pan/tilt limits and is the only runtime servo writer.
10. The guided expression test never initializes the mobility controller.

## Source of Freenove mappings

The adapter was checked against the official [Freenove 4WD Smart Car Kit for Raspberry Pi repository](https://github.com/Freenove/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi):

- `motor.py`: `Ordinary_Car.set_motor_model(front_left, rear_left, front_right, rear_right)`
- `servo.py`: `Servo.set_servo_pwm(channel, angle)`
- `ultrasonic.py`: `Ultrasonic.get_distance()`
- `adc.py`: `ADC.read_adc(channel)`
- `parameter.py`: validated connector, PCB, and Pi version file

R08Y imports those public interfaces at runtime. The Freenove checkout remains the single source for board-specific pin and PWM assignments.
