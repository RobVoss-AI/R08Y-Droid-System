# R08Y Build Guide

Step-by-step instructions for building your R08Y droid from scratch.

---

## Part 1: Hardware Assembly

### What You Need

**Minimum build** (voice + face recognition, no tracking):
- Raspberry Pi 4B (4GB recommended)
- 32GB microSD card
- 5V 3A USB-C power supply
- USB webcam with built-in mic (Logitech C270: ~$25)
- USB speaker or 3.5mm speaker

**Full build** (add camera tracking):
- Everything above, plus:
- 2x SG90 micro servo motors (~$3 each)
- Pan/tilt servo bracket (~$5)
- PCA9685 16-channel PWM servo board (~$6)
- Jumper wires (female-to-female)
- Small breadboard (optional)

**Extended build** (add sensors and LEDs):
- HC-SR04 ultrasonic distance sensor (~$3)
- NeoPixel/WS2812 LED ring, 12 pixels (~$8)
- 1x 1kΩ resistor, 1x 2kΩ resistor (for HC-SR04 voltage divider)
- 1x 470Ω resistor (for NeoPixel data line)

### Wiring Diagram

#### PCA9685 Servo Board → Pi

```
PCA9685        Raspberry Pi 4B
────────       ──────────────
VCC    ──────► 3.3V (Pin 1)
GND    ──────► GND  (Pin 6)
SDA    ──────► GPIO 2 / SDA (Pin 3)
SCL    ──────► GPIO 3 / SCL (Pin 5)
V+     ──────► External 5V supply (NOT from Pi)
```

**Important**: Power the servos from a separate 5V supply connected to the PCA9685 V+ terminal, not from the Pi's 5V pin. Servos draw too much current and will brown out the Pi.

#### Servo Connections to PCA9685

```
PCA9685 Channel 0 ──► Pan servo (horizontal rotation)
PCA9685 Channel 1 ──► Tilt servo (vertical rotation)
```

Each servo has 3 wires:
- Brown/Black → GND
- Red → V+
- Orange/Yellow → Signal

#### HC-SR04 Distance Sensor → Pi

The HC-SR04 runs at 5V but the Pi's GPIO is 3.3V. Use a voltage divider on ECHO:

```
HC-SR04        Raspberry Pi 4B
───────        ──────────────
VCC    ──────► 5V (Pin 2)
GND    ──────► GND (Pin 9)
TRIG   ──────► GPIO 23 (Pin 16)
ECHO   ──┬──► 1kΩ resistor ──► GPIO 24 (Pin 18)
          └──► 2kΩ resistor ──► GND
```

#### NeoPixel LED Ring → Pi

```
NeoPixel       Raspberry Pi 4B
────────       ──────────────
VCC    ──────► 5V (Pin 4)
GND    ──────► GND (Pin 14)
DIN    ──────► 470Ω resistor ──► GPIO 18 (Pin 12)
```

---

## Part 2: Software Setup

### Step 1: Flash Raspberry Pi OS

1. Download [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Choose **Raspberry Pi OS Lite (64-bit)** — no desktop needed
3. Click the gear icon to pre-configure:
   - Set hostname: `r08y`
   - Enable SSH
   - Set username/password
   - Configure WiFi
4. Flash to your microSD card
5. Insert card into Pi and power on

### Step 2: Connect via SSH

```bash
ssh pi@r08y.local
# or use the IP address: ssh pi@192.168.x.x
```

### Step 3: Run the Installer

```bash
git clone https://github.com/RobVoss-AI/R08Y-Droid-System.git
cd R08Y-Droid-System
bash install.sh
```

This takes 30-90 minutes (dlib compilation is slow on Pi 4). The installer:
- Enables I2C for the servo board
- Installs system packages (OpenCV, PortAudio, ffmpeg, mpv, i2c-tools)
- Creates a Python virtual environment at `/opt/r08y/`
- Compiles dlib with ARM NEON optimizations (temporarily increases swap)
- Installs all Python dependencies
- Sets up the systemd service

### Step 4: Get Your API Keys

You need at minimum an **Anthropic API key** for Claude:

1. **Anthropic** (Claude AI): [console.anthropic.com](https://console.anthropic.com/) → Create API key
2. **Groq** (Free Whisper STT): [console.groq.com](https://console.groq.com/) → Create API key
3. **Picovoice** (Wake word): [console.picovoice.ai](https://console.picovoice.ai/) → Get access key

Edge TTS and Open-Meteo weather are free and don't need keys.

### Step 5: Configure

```bash
nano /opt/r08y/config.yaml
```

Fill in your API keys:

```yaml
ai:
  api_key: sk-ant-api03-xxxxx      # Your Anthropic key

stt:
  provider: groq
  groq_api_key: gsk_xxxxx          # Your Groq key

wake_word:
  porcupine_access_key: xxxxx      # Your Picovoice key
```

Or use environment variables instead:

```bash
export R08Y_ANTHROPIC_API_KEY=sk-ant-api03-xxxxx
export R08Y_GROQ_API_KEY=gsk_xxxxx
export R08Y_PICOVOICE_KEY=xxxxx
```

### Step 6: Verify Hardware

```bash
cd /opt/r08y
./venv/bin/python tests/test_hardware.py
```

Expected output:
```
=== R08Y Hardware Check ===

[Camera]  OK — 640x480
[I2C]    OK — PCA9685 found at 0x40
[Mic]    OK — 'USB Audio Device' (index 1)
[Speaker] OK — beep played

=== Summary ===
  Camera: PASS
  I2C: PASS
  Microphone: PASS
  Speaker: PASS
```

If I2C shows "NOT FOUND", the PCA9685 isn't wired or I2C isn't enabled. Run:
```bash
sudo raspi-config nonint do_i2c 0
sudo i2cdetect -y 1
```

### Step 7: First Run

```bash
cd /opt/r08y
./venv/bin/python -m r08y
```

You should hear: **"R08Y online. Ready when you are."**

Try saying:
- **"Hey R08Y"** (or your configured wake word) → "What's the weather?" → hear the forecast
- Look at the camera → servos should track your face
- **"Hey R08Y"** → "Remember my face, I'm Rob" → "Got it! I'll remember you as Rob."
- **"Hey R08Y"** → "Who am I?" → "You're Rob!"

### Step 8: Run as a Service

```bash
sudo systemctl start r08y
sudo systemctl status r08y

# View live logs
journalctl -u r08y -f

# Auto-start on boot (already enabled by installer)
sudo systemctl enable r08y

# Stop
sudo systemctl stop r08y
```

---

## Part 3: Using Skills

### Voice Commands Quick Reference

Say the wake word first, then any of these:

**Camera:**
```
"nod yes"               → R08Y nods the camera
"shake no"              → R08Y shakes the camera
"dance"                 → R08Y does a camera dance
"look left / right"     → Points camera in that direction
"start patrol"          → Sweep through saved waypoints
"save this position"    → Add current angle to patrol route
```

**Voice:**
```
"pirate voice"          → Switches to pirate personality
"british voice"         → British TTS voice
"normal voice"          → Back to default
"start dictating"       → All speech saved to file (not sent to AI)
"stop dictating"        → Ends dictation, saves file
```

**Vision:**
```
"take a photo"          → Captures and saves a JPEG
"scan QR"               → Starts scanning for QR codes
"what do you see"       → Lists detected objects (needs model)
"how do I look"         → Reads your facial expression
```

**Utility:**
```
"how are you feeling"   → CPU temp, memory, disk, uptime
"set a timer for 5 minutes" → Countdown with spoken alert
"what's the weather"    → Current conditions from Open-Meteo
"turn on the lights"    → Sends command to Home Assistant
```

**Monitoring:**
```
"start monitoring"      → Activates motion detection
"start proximity"       → HC-SR04 distance alerts
"lights off / on"       → NeoPixel LED control
"set color blue"        → Change LED color
```

### Configuring Skills

In `config.yaml`, the `skills` section controls each skill:

```yaml
skills:
  # Disable skills you don't want
  disabled: ["home_assistant", "led_controller"]

  # Per-skill configuration
  weather:
    latitude: 40.7128
    longitude: -74.0060
    location_name: New York

  home_assistant:
    url: http://homeassistant.local:8123
    token: your_long_lived_access_token
    entities:
      lights: light.living_room
      bedroom light: light.bedroom

  distance_sensor:
    trig_pin: 23
    echo_pin: 24
    approach_threshold: 50  # centimeters

  led_controller:
    num_pixels: 12
    brightness: 30
```

### Creating Your Own Skill

1. Create a directory: `r08y/skills/my_skill/`
2. Create `__init__.py` with a `Skill` class:

```python
from r08y.skills._base import BaseSkill, SkillManifest, VoiceCommand

class Skill(BaseSkill):
    @property
    def manifest(self):
        return SkillManifest(
            name="my_skill",
            version="1.0.0",
            description="My custom skill",
            requires=set(),  # or {"camera"}, {"servos"}, {"tts"}, {"network"}, {"gpio"}
            commands=[
                VoiceCommand(
                    triggers=["do my thing", "activate skill"],
                    handler="cmd_do_it",
                ),
            ],
        )

    async def cmd_do_it(self, text: str, user_name: str):
        # text = full transcribed speech
        # user_name = recognized face name or "unknown"
        return "I did the thing!"  # spoken via TTS
```

3. Restart R08Y — your skill loads automatically.

**Advanced: Hook into pipelines**

```python
from r08y.skills._base import BaseSkill, PipelineHook, SkillManifest

class Skill(BaseSkill):
    @property
    def manifest(self):
        return SkillManifest(
            name="my_vision_skill",
            pipeline_hooks=[PipelineHook.VISION_FRAME],
            vision_frame_interval=0.5,  # called every 0.5 seconds
        )

    async def on_vision_frame(self, frame):
        # frame is a numpy array (BGR, from OpenCV)
        # process it, emit events, etc.
        pass
```

**Advanced: Inter-skill events**

```python
# Emit an event
await self.ctx.event_bus.emit("my_skill.something_happened", source=self.name, data="value")

# Listen for events (declare in manifest, handler is auto-registered)
manifest = SkillManifest(
    listens_events=["motion.detected"],  # will call self.on_motion_detected()
)

async def on_motion_detected(self, event):
    data = event.payload
    # react to motion
```

---

## Part 4: Troubleshooting

### Camera not detected
```bash
ls /dev/video*              # Should show /dev/video0
v4l2-ctl --list-devices     # List all video devices
```
If empty, the webcam isn't recognized. Try a different USB port or webcam.

### No audio output
```bash
aplay -l                    # List audio playback devices
speaker-test -c 2           # Test speakers
```
For USB speakers on Pi 3B/3B+, check ALSA config in `~/.asoundrc`.

### Servos not moving
```bash
sudo i2cdetect -y 1         # Should show 40 for PCA9685
```
If no 40, check wiring: SDA → GPIO 2, SCL → GPIO 3, and I2C is enabled.

### dlib compilation fails
The Pi needs enough swap space. The installer handles this, but if it fails:
```bash
sudo sed -i 's/CONF_SWAPSIZE=.*/CONF_SWAPSIZE=2048/' /etc/dphys-swapfile
sudo systemctl restart dphys-swapfile
pip install dlib --no-cache-dir
```

### Wake word not triggering
- Check your Picovoice access key is valid
- Falls back to RMS noise detection if Porcupine isn't configured
- Adjust `audio.rms_threshold` in config (lower = more sensitive)

### High CPU usage
- Set `face_detection.method: haar` (faster than dnn)
- Increase skill `vision_frame_interval` values
- Reduce camera resolution: `camera.width: 320`, `camera.height: 240`

### View logs
```bash
# If running as service
journalctl -u r08y -f

# If running directly
python -m r08y -c config.yaml  # logs to stdout by default

# Enable debug logging
# In config.yaml:
logging:
  level: DEBUG
```

---

## Part 5: What's Next

### Add Mobility
The `MotorController` abstract base class in `servo_controller.py` is designed for extension. The `mobility.py` stub is ready for you to implement wheel control using an L298N motor driver or similar.

### Custom Wake Word
1. Go to [Picovoice Console](https://console.picovoice.ai/)
2. Create a custom keyword: "Hey R08Y"
3. Download the `.ppn` file
4. Set `wake_word.keyword_path` in config to point to it

### Better Object Detection
Download a MobileNet SSD model into `data/skills/object_detect/`:
```bash
cd /opt/r08y/data/skills/object_detect
wget https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/MobileNetSSD_deploy.prototxt
wget https://drive.google.com/uc?id=0B3gersZ2cHIxRm5PMWRoTkdHdHc -O MobileNetSSD_deploy.caffemodel
```

### Better Emotion Detection
The built-in emotion detector uses simple heuristics. For real accuracy, install [DeepFace](https://github.com/serengil/deepface) or [FER](https://github.com/justinshenk/fer) and replace the `_analyze_emotion_sync` method in `emotion_detect`.

### Add a Display
A small LCD (3.5" SPI or 7" HDMI) could show an animated face like Meckie. This would be a new skill that hooks into `DroidState` to show expressions matching the droid's current state.

### Home Assistant Setup
1. In Home Assistant: Settings → Long-Lived Access Tokens → Create Token
2. Add to `config.yaml`:
   ```yaml
   skills:
     home_assistant:
       url: http://homeassistant.local:8123
       token: your_token_here
       entities:
         lights: light.living_room
   ```
3. Say: "Hey R08Y, turn on the lights"
