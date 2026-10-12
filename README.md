# R08Y Droid System

R08Y turns a Raspberry Pi 4B and Freenove 4WD mecanum chassis into a voice-controlled AI droid with vision, face tracking, sensors, and guarded movement.

## Status

In development. XVF3800 capture and speaker playback have been verified on the Pi. The ROBY wake-word path, interaction director, expression kernel, and mock-hardware suite are desktop-verified. Live acceptance still remains for ROBY detection, coordinated voice/body-language interaction, motor direction, and camera selection.

## Stack

- Python 3.11+ with `asyncio` pipelines for vision, voice, and skills
- Official Freenove FNK0043 Python modules behind R08Y hardware adapters
- OpenCV/Picamera2, ALSA/PyAudio, PipeWire, Porcupine, Whisper, Claude, and Edge TTS
- SQLite for face encodings and conversation history
- `systemd` service for Debian 13 and Raspberry Pi OS, including the user PipeWire runtime

## Local development

```bash
git clone https://github.com/RobVoss-AI/R08Y-Droid-System.git
cd R08Y-Droid-System
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q
cp config.example.yaml config.yaml
.venv/bin/python -m r08y --config config.yaml --check-config
```

The unit tests use fakes and do not touch motors, servos, audio, or GPIO.

## Deploy

Target: Raspberry Pi 4B running Debian 13 or Raspberry Pi OS.

```bash
bash install.sh
cd /opt/r08y
./venv/bin/python -m r08y --config config.yaml --smoke-test
sudo systemctl start r08y
```

Do not start the service until the passive smoke test passes. Follow [HOWTO.md](HOWTO.md) for the speaker-first deployment sequence and the guarded wheels-raised motor exercise.

## Notes for maintainers

- R08Y does not copy or guess Freenove pin and PWM mappings. It loads the official `motor.py`, `servo.py`, `ultrasonic.py`, and `adc.py` modules from the Freenove `Code/Server` directory.
- Haar face detection resolves both pip OpenCV data and Debian's `/usr/share/opencv4/haarcascades` location. The installer adds `opencv-data` when available; missing cascade data disables vision without taking voice offline.
- The official Freenove `params.json` must identify the connector, controller PCB, and Pi generation before ADC light-sensor initialization. R08Y refuses to trigger the upstream interactive prompt inside the service.
- Numeric audio indexes are unstable across reboots. Input and output default to a strict name match for `XVF3800`; R08Y will not silently speak through another sink.
- The XVF3800 uses a name-resolved `arecord` stream at its verified native S16_LE, 16 kHz, two-channel format. Reads have a hard timeout, and R08Y downmixes to 16 kHz mono for wake word and STT processing. Other microphones retain the PyAudio fallback.
- Startup reads an actual capture chunk. If the XVF3800 returns an immediate I/O error or no samples before the deadline, R08Y closes capture and reports the known physical USB unplug/replug recovery.
- The production wake word is `ROBY`. R08Y requires a Raspberry Pi Porcupine `.ppn` model and fails closed when the model or Picovoice AccessKey is missing. RMS sound triggering remains an explicit development option and is disabled by default.
- Custom wake-word models and API keys are local deployment assets. The repository ignores `.ppn` files, and secrets stay in `/etc/r08y/r08y.env`.
- One expression controller owns every runtime pan/tilt write. Face tracking and skills submit bounded gaze targets or named gestures to its queue.
- Conversational states add restrained idle, listening, thinking, and speaking motion. `--expression-test` exercises only pan/tilt and never initializes wheel movement.
- The interaction director adds a quiet acknowledgement cue after ROBY, chooses at most one meaning-aware gesture per response, and leaves neutral replies unembellished. `--interaction-test` auditions the speaker and pan/tilt together without initializing mobility.
- No-face patrol scanning is disabled by default. After losing a face, R08Y returns its gaze to center and retains only the restrained idle sway; full-range scanning requires `tracker.patrol_enabled: true`.
- Forward movement requires a valid ultrasonic reading and at least 30 cm of clearance by default. Every timed command stops in `finally`, and a second watchdog stops stale commands.
- `power.ups_enabled` stays `false` until the UPS HAT and battery are tested as a separate change.
- Negative `mobility.wheel_scale` values reverse individual wheels. Confirm direction only with all four wheels clear of the surface.
- API keys belong in `/etc/r08y/r08y.env`, never in `config.yaml` or logs.

## Architecture

```text
XVF3800 USB input -> ROBY model -> record -> Whisper STT
                                             |
                                             v
Freenove sensors -> skill commands ------> Claude -> Edge TTS -> XVF3800 output
       |                 |
       |                 +-> guarded mecanum mobility
       v
camera -> face detection/recognition -> PID gaze target ----+
skills -> named gestures ----------------------------------+-> expression queue -> pan/tilt
interaction director -> turn cue + response gesture -------+
voice -> listening/thinking/speaking state ----------------+
```

The hardware boundary is explicit:

```text
r08y/hardware/freenove.py        verifies and loads official kit modules
r08y/modules/servo_controller.py Freenove pan/tilt or explicit GPIO adapter
r08y/modules/expression_controller.py single motion owner, queue, smoothing, and body language
r08y/modules/sensors.py          ultrasonic and light-sensor adapters
r08y/modules/mobility.py         mecanum mixing, clearance guard, watchdog
r08y/modules/audio_devices.py    named ALSA capture, PortAudio fallback, and PipeWire routing
r08y/modules/health.py           runtime component health report
r08y/diagnostics.py              passive and opt-in Pi smoke tests
```

See [docs/HARDWARE_MAP.md](docs/HARDWARE_MAP.md) for the full reuse, modify, and add decision record.

## Runtime behavior

R08Y runs two main pipelines:

```text
Vision: camera -> detection -> gaze target -> recognition -> vision skill hooks
Voice:  wake word -> capture -> STT -> command skill or Claude -> TTS
Motion: gaze + named gesture + turn state -> one bounded pan/tilt writer
```

Skills discover themselves from `r08y/skills/*/__init__.py`. Hardware-dependent skills load only when their required adapter reports available. A failed skill setup or startup is removed from command routing instead of remaining partly active.

New chassis commands include:

- “move forward,” “move backward,” and “back up”
- “strafe left” and “strafe right”
- “turn left” and “turn right”
- “stop wheels” and “emergency stop”
- “show your moves”
- “how far,” “check light sensors,” and “hardware status”

Movement is short and speed-limited. Spoken forward commands are rejected when the ultrasonic sensor is unavailable, returns no reading, or sees an obstacle inside the configured stop distance.

## Configuration

Copy `config.example.yaml` to `config.yaml`. Put secrets in the environment file created by the installer:

```text
R08Y_ANTHROPIC_API_KEY=
R08Y_GROQ_API_KEY=
R08Y_OPENAI_API_KEY=
R08Y_PICOVOICE_KEY=
R08Y_WAKE_WORD_PATH=/opt/r08y/wake_words/roby.ppn
R08Y_FREENOVE_PATH=
```

The minimum conversational path uses Anthropic, Groq or OpenAI for transcription, a Raspberry Pi Porcupine model for the ROBY wake word, and Edge TTS for speech. Generate the platform-specific custom model in [Picovoice Console](https://console.picovoice.ai/) and keep it outside Git. Weather uses Open-Meteo without an API key.

The example uses [`claude-sonnet-5-5`](https://platform.claude.com/docs/en/models/overview) and [`whisper-large-v3`](https://console.groq.com/docs/model/whisper-large-v3). Both identifiers were checked against their providers’ model documentation on October 4, 2026.

## Verification commands

```bash
# Desktop-safe tests
.venv/bin/python -m pytest -q

# Configuration only; no hardware access
./venv/bin/python -m r08y --config config.yaml --check-config

# Passive Pi discovery; no motors, servo movement, camera, or audio playback
./venv/bin/python -m r08y --config config.yaml --smoke-test

# Guided two-second microphone sample and speaker tone; no other hardware
./venv/bin/python -m r08y --config config.yaml --audio-test

# Detect ROBY with the configured custom model; no camera, servos, or motors
./venv/bin/python -m r08y --config config.yaml --wake-word-test

# Speak a fixed sentence with the configured voice, rate, pitch, and volume
./venv/bin/python -m r08y --config config.yaml --speech-test

# Audition a voice without changing config.yaml
./venv/bin/python -m r08y --config config.yaml --speech-test \
  --speech-voice en-US-AndrewMultilingualNeural \
  --speech-rate=+1% --speech-pitch=+2Hz

# Guided personality gestures; pan/tilt only, wheels never initialize
./venv/bin/python -m r08y --config config.yaml --expression-test

# Coordinated acknowledgement, speech, and body language; no wheels
./venv/bin/python -m r08y --config config.yaml --interaction-test

# Camera, pan/tilt home, sensors, microphone, and speaker tone
./venv/bin/python -m r08y --config config.yaml --smoke-test --exercise-hardware

# Adds six low-speed movement patterns. Raise every wheel first.
./venv/bin/python -m r08y --config config.yaml --smoke-test \
  --exercise-hardware --exercise-mobility --confirm-wheels-raised
```

The program exits nonzero when a required smoke-test check fails. `--json` produces machine-readable results.

## Project structure

```text
r08y/
├── main.py                 runtime orchestration and graceful shutdown
├── config.py               typed YAML configuration and validation
├── diagnostics.py          Pi smoke-test command path
├── hardware/               official Freenove runtime bridge
├── modules/                vision, voice, AI, interaction, motion, and health
└── skills/                 auto-discovered command and pipeline plugins
tests/                      mock/fake unit and integration-boundary tests
docs/HARDWARE_MAP.md        reuse/modify/add hardware decision record
HOWTO.md                    exact Pi deployment and acceptance procedure
```

## Third-party hardware source

The chassis adapter targets Freenove’s official [4WD Smart Car Kit for Raspberry Pi repository](https://github.com/Freenove/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi). That upstream project owns its board-specific mappings and retains its own license.
