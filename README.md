# R08Y Droid System

A Raspberry Pi 4B AI companion with facial recognition, voice interaction, servo-driven camera tracking, and a pluggable skill system. Built with a hybrid architecture — real-time vision and tracking run on-device while speech and AI use cloud APIs.

```
┌─────────────────── Raspberry Pi 4B ───────────────────┐
│                                                        │
│  USB Webcam ──► Camera ──► Face Detector               │
│     (mic)         │             │                      │
│                   │       Face Recognizer              │
│  PyAudio ◄────────┘       (dlib encodings)             │
│     │                          │                       │
│  Wake Word ──► Record    Face Tracker (PID)            │
│  (porcupine)                   │                       │
│                          Servo Controller              │
│                          (PCA9685 / GPIO)              │
│                                │                       │
│       ┌──── Skills (16 plugins) ────┐                  │
│       │ gestures, patrol, motion,   │                  │
│       │ weather, timer, QR, LEDs... │                  │
│       └─────────────────────────────┘                  │
└────────────────────────────────────────────────────────┘
         │ Cloud APIs
         ▼
  Whisper STT    Claude API    edge-tts
  (Groq/OpenAI)  (Anthropic)  (Microsoft)
```

## Features

- **Facial recognition** — Detect, learn, and recognize faces using dlib 128D encodings stored in SQLite
- **Voice interaction** — Wake word detection (Porcupine), speech-to-text (Whisper), AI conversation (Claude), text-to-speech (Edge TTS)
- **Camera tracking** — PID-controlled pan/tilt servos follow faces in real-time; patrol mode when idle
- **16 pluggable skills** — Gestures, motion detection, QR scanning, weather, timers, Home Assistant, dictation, and more
- **Graceful degradation** — Every module and skill is optional; R08Y works with whatever hardware is connected

## Hardware

### Required (~$85)

| Component | Notes |
|-----------|-------|
| Raspberry Pi 4B | 2GB minimum, 4GB+ recommended |
| USB webcam | Logitech C270 or similar (includes mic) |
| USB/3.5mm speaker | For TTS audio output |
| 16GB+ microSD card | 32GB recommended |
| 5V 3A USB-C power supply | Official Pi 4 PSU recommended |

### Optional

| Component | Purpose |
|-----------|---------|
| 2x SG90 servo motors + pan/tilt bracket | Camera tracking |
| PCA9685 PWM servo board | Precise I2C servo control |
| HC-SR04 ultrasonic sensor | Proximity detection |
| NeoPixel/WS2812 LED ring | Status lighting and mood effects |
| Bluetooth speaker | Wireless audio |

## Quick Start

```bash
# Clone the repo
git clone https://github.com/RobVoss-AI/R08Y-Droid-System.git
cd R08Y-Droid-System

# Run the installer (on Pi)
bash install.sh

# Edit your config
nano /opt/r08y/config.yaml

# Test it
cd /opt/r08y && ./venv/bin/python -m r08y

# Run as a service
sudo systemctl start r08y
sudo systemctl status r08y
```

See [HOWTO.md](HOWTO.md) for the full build guide.

## Project Structure

```
r08y/
├── main.py                 # Orchestrator — two async pipelines + skill manager
├── config.py               # Typed YAML config with env var overrides
├── modules/                # Core hardware/API modules
│   ├── camera.py           # Async OpenCV webcam capture
│   ├── face_detector.py    # Haar cascade (fast) / DNN (accurate)
│   ├── face_recognizer.py  # dlib face encodings + SQLite
│   ├── face_tracker.py     # PID controller for servo tracking
│   ├── servo_controller.py # PCA9685 → GPIO → Mock fallback chain
│   ├── voice_input.py      # Wake word + audio recording
│   ├── speech_to_text.py   # Whisper API (OpenAI / Groq)
│   ├── ai_brain.py         # Claude API with conversation history
│   ├── text_to_speech.py   # Edge TTS with speech queue
│   └── mobility.py         # Stub for future wheel motors
└── skills/                 # Pluggable skill system
    ├── _base.py            # BaseSkill ABC + SkillManifest
    ├── _bus.py             # EventBus (async pub/sub)
    ├── _registry.py        # Voice command routing
    ├── camera_gestures/    # Nod, shake, dance, look directions
    ├── patrol_routes/      # Saved scan patterns
    ├── motion_detect/      # Frame differencing security alerts
    ├── sound_effects/      # Audio cues for events
    ├── voice_profiles/     # Switch TTS personalities
    ├── dictation/          # Speak-to-text-file mode
    ├── object_detect/      # MobileNet SSD object detection
    ├── emotion_detect/     # Facial expression reading
    ├── photo_capture/      # Take and save photos
    ├── qr_reader/          # QR code / barcode scanning
    ├── system_monitor/     # CPU temp, memory, disk, uptime
    ├── timer_alarm/        # Countdown timers with alerts
    ├── weather/            # Open-Meteo weather forecasts
    ├── home_assistant/     # Smart home control via HA API
    ├── distance_sensor/    # HC-SR04 proximity detection
    └── led_controller/     # NeoPixel status/mood lighting
```

## Skills

Skills are self-contained plugins that register their own voice commands and pipeline hooks. They auto-discover at startup — adding a new skill never requires editing core code.

| Skill | Voice Commands | Requires |
|-------|---------------|----------|
| `camera_gestures` | "nod yes", "shake no", "dance", "look left" | servos |
| `patrol_routes` | "start patrol", "save this position" | servos |
| `motion_detect` | "start monitoring", "any motion" | camera |
| `sound_effects` | "beep", "happy sound", "alert sound" | — |
| `voice_profiles` | "pirate voice", "british voice", "normal voice" | tts |
| `dictation` | "start dictating", "stop dictating", "read it back" | tts |
| `object_detect` | "what do you see", "count people" | camera |
| `emotion_detect` | "how do I look", "read my expression" | camera |
| `photo_capture` | "take a photo", "say cheese" | camera |
| `qr_reader` | "scan QR", "last QR code" | camera |
| `system_monitor` | "how are you feeling", "CPU temp", "uptime" | — |
| `timer_alarm` | "set a timer for 5 minutes", "time left" | tts |
| `weather` | "what's the weather", "temperature outside" | network |
| `home_assistant` | "turn on the lights", "toggle the fan" | network |
| `distance_sensor` | "how far", "start proximity alert" | gpio |
| `led_controller` | "set color red", "lights off", "brightness 50" | gpio |

### Creating a Custom Skill

Create `r08y/skills/my_skill/__init__.py`:

```python
from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand

class Skill(BaseSkill):
    @property
    def manifest(self):
        return SkillManifest(
            name="my_skill",
            description="Does something cool",
            commands=[
                VoiceCommand(triggers=["do the thing"], handler="cmd_do_it"),
            ],
        )

    async def cmd_do_it(self, text, user_name):
        return "Done!"
```

That's it. R08Y discovers and loads it automatically on next startup.

## Configuration

Copy `config.example.yaml` to `config.yaml` and fill in your API keys:

```yaml
ai:
  api_key: YOUR_ANTHROPIC_KEY_HERE    # or set R08Y_ANTHROPIC_API_KEY env var

stt:
  provider: groq                       # "groq" (free tier) or "openai"
  groq_api_key: YOUR_GROQ_KEY_HERE    # or set R08Y_GROQ_API_KEY env var

wake_word:
  porcupine_access_key: YOUR_KEY      # or set R08Y_PICOVOICE_KEY env var
```

All API keys can also be set via environment variables (see `config.example.yaml`).

## API Keys Needed

| Service | Purpose | Free Tier |
|---------|---------|-----------|
| [Anthropic](https://console.anthropic.com/) | Claude AI conversation | Pay-per-use |
| [Groq](https://console.groq.com/) | Whisper speech-to-text | Yes, generous |
| [Picovoice](https://console.picovoice.ai/) | Wake word detection | Yes, 3 keywords |
| Edge TTS | Text-to-speech | Free (no key needed) |
| [Open-Meteo](https://open-meteo.com/) | Weather data | Free (no key needed) |

## How It Works

R08Y runs two concurrent async pipelines:

**Vision Pipeline** (continuous, ~15 FPS):
```
camera → face detection → servo tracking → face recognition (throttled)
                        → skill hooks (motion detect, QR reader, etc.)
```

**Voice Pipeline** (event-driven):
```
wake word → record speech → STT → skill commands → Claude AI → TTS → speak
```

Pipelines share state through `DroidState` — the voice pipeline knows who the droid is looking at, and can include visual context in conversations with Claude.

## License

MIT

## Credits

Inspired by [meckie.ai](https://meckie.ai/). Built by Rob Voss with Claude Code.
