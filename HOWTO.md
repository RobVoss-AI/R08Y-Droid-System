# R08Y Pi Deployment Guide

This guide is for Rob’s current build: Raspberry Pi 4B on Debian 13, Freenove 4WD mecanum chassis, Freenove camera and pan/tilt, ultrasonic and light sensors, XVF3800 USB four-microphone array, and a Seeed-compatible 4-ohm 5-watt speaker.

The UPS HAT and battery are deliberately excluded from this procedure.

## 1. Prepare the robot

1. Leave the UPS HAT disconnected.
2. Use the direct USB-C power setup that previously reported `throttled=0x0`.
3. Power the system down before connecting the speaker.
4. Connect the speaker only to the XVF3800’s documented JST speaker interface. Do not connect it to a Pi GPIO header or the 3.5 mm headphone jack.
5. Support the chassis so all four wheels are clear of the table. Keep a hand near the physical power control during the first motor exercise.

Seeed documents the XVF3800 speaker connector as a JST interface supporting a compatible 5-watt speaker. Check the current board diagram before insertion: [XVF3800 getting-started guide](https://wiki.seeedstudio.com/respeaker_xvf3800_introduction/).

## 2. Connect and check the known-good base

From your Mac:

```bash
ssh rob@freenove.local
```

On the Pi:

```bash
vcgencmd get_throttled
arecord -l
wpctl status -n
sudo i2cdetect -y 1
```

Expected:

- `throttled=0x0`
- an ALSA capture device whose name contains `XVF3800` (its card number may change)
- an XVF3800 source and sink
- I2C addresses `40` for the PWM controller and `48` for the ADC

Stop if the Pi reports current undervoltage or throttling. Historical throttling bits should also be resolved before motor testing.

Open the mixer and confirm the already verified speaker level:

```bash
alsamixer
```

Press `F6`, select the XVF3800 by name, and keep `PCM 1` at `100`. Press `Esc`, then save the working mixer state:

```bash
sudo alsactl store
```

## 3. Verify the official Freenove source

R08Y calls the official Freenove API so it never has to guess board mappings.

```bash
cd ~
test -d Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi/Code/Server || \
  git clone https://github.com/Freenove/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi.git
cd ~/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi/Code/Server
ls motor.py servo.py ultrasonic.py adc.py parameter.py
```

If `params.json` already exists from the working Freenove installation, inspect it:

```bash
python3 -m json.tool params.json
```

It must contain valid values for `Connect_Version`, `Pcb_Version`, and `Pi_Version`. Do not infer these values. If it is missing or wrong, compare the controller board with the official PCB images in the Freenove README, then run:

```bash
cd ~/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi/Code/Server
python3 parameter.py
```

Enter the verified connector and PCB versions when prompted. A Pi 4 is the upstream Pi version `1` and is detected automatically. If the script asks whether to re-enter the values after creating the file, answer `no`.

## 4. Install R08Y

For a first clone:

```bash
cd ~
git clone https://github.com/RobVoss-AI/R08Y-Droid-System.git
cd R08Y-Droid-System
git switch codex/roby-wake-word
bash install.sh
```

For an existing clone:

```bash
cd ~/R08Y-Droid-System
sudo systemctl stop r08y 2>/dev/null || true
git fetch origin
git switch codex/roby-wake-word
git pull --ff-only
bash install.sh
```

The branch command selects the ROBY wake-word work stacked on the expression and audio-integration pull requests. After the pull requests are merged, switch back to `main` and use the same pull/install sequence.

The installer creates `/opt/r08y`, `/etc/r08y/r08y.env`, and the `r08y.service` unit. It preserves an existing `/opt/r08y/config.yaml`.

## 5. Configure secrets and hardware

Open the protected environment file locally on the Pi:

```bash
sudo nano /etc/r08y/r08y.env
```

Set the keys you use. Do not paste them into chat or commit them:

```text
R08Y_ANTHROPIC_API_KEY=
R08Y_GROQ_API_KEY=
R08Y_PICOVOICE_KEY=
R08Y_WAKE_WORD_PATH=/opt/r08y/wake_words/roby.ppn
R08Y_FREENOVE_PATH=/home/rob/Freenove_4WD_Smart_Car_Kit_for_Raspberry_Pi/Code/Server
```

Review the installed configuration:

```bash
sudo nano /opt/r08y/config.yaml
```

Keep these safety defaults for first run:

```yaml
audio:
  sample_rate: 16000
  channels: 1
  device_index: null
  device_name: XVF3800
  require_device_match: true
  capture_backend: auto
  hardware_sample_rate: 16000
  hardware_channels: 2

wake_word:
  engine: porcupine
  phrase: ROBY
  keyword_path: /opt/r08y/wake_words/roby.ppn
  sensitivity: 0.5
  allow_rms_fallback: false

tts:
  playback_command: pw-play
  output_device_name: XVF3800
  voice: en-US-AndrewMultilingualNeural
  rate: "+1%"
  pitch: "+2Hz"
  volume: "+0%"
  set_pipewire_default: true
  require_output_device_match: true

mobility:
  max_duty: 2200
  max_duration: 2.0
  command_timeout: 2.5
  obstacle_stop_cm: 30.0
  require_distance_for_forward: true

power:
  ups_enabled: false
```

## 6. Add the custom ROBY model

Porcupine custom models are platform-specific. In [Picovoice Console](https://console.picovoice.ai/), create an English wake word spelled `ROBY`, select Raspberry Pi as the target platform, and download the `.ppn` file. Keep the AccessKey in `/etc/r08y/r08y.env`; do not paste it into chat or commit it.

Rename the downloaded file to `roby.ppn`. From a second Mac terminal, copy it to the Pi:

```bash
scp ~/Downloads/roby.ppn rob@freenove.local:/tmp/roby.ppn
```

On the Pi, install it in the protected runtime directory:

```bash
sudo install -o rob -g rob -m 0640 \
  /tmp/roby.ppn /opt/r08y/wake_words/roby.ppn
ls -l /opt/r08y/wake_words/roby.ppn
```

The installer makes the environment file group-readable by the install user. Load it into the current shell without printing its values:

```bash
set -a
source /etc/r08y/r08y.env
set +a
```

## 7. Validate in seven gates

### Gate A: configuration only

```bash
cd /opt/r08y
./venv/bin/python -m r08y --config config.yaml --check-config
```

Expected: `Configuration is valid.` No hardware moves.

### Gate B: passive discovery

```bash
./venv/bin/python -m r08y --config config.yaml --smoke-test
```

Expected passes:

- configuration
- official Freenove library
- Freenove parameter file and PCB version
- PCA9685 at `0x40` and ADS7830 at `0x48`
- XVF3800 ALSA capture hardware discovered by name
- XVF3800 PipeWire source and sink
- Pi power status `throttled=0x0`

The UPS result should remain `SKIP`.

### Gate C: guided microphone and speaker test

This step does not start the camera, servos, sensors, or motors:

```bash
./venv/bin/python -m r08y --config config.yaml --audio-test
```

Wait for the `READY` line. Say “R08Y audio check” once. Sampling stops automatically after two seconds, and the terminal tells you when to stop talking. A short 660 Hz speaker tone follows.

Confirm both results pass and that the tone is clearly audible. If the tone command passes but nothing is audible, inspect `wpctl status -n` and XVF3800 playback volume. Do not reflash firmware during this first pass; headphone playback already proved the USB output path.

After the tone passes, audition the configured speaking voice without starting any other hardware:

```bash
./venv/bin/python -m r08y --config config.yaml --speech-test
```

This says one fixed sentence using `tts.voice`, `rate`, `pitch`, and `volume`. Adjust one value at a time in `config.yaml`, validate the configuration, and rerun the speech test. Keep speaker loudness in the XVF3800 mixer; use `tts.volume` only for relative speech adjustment.

R08Y resolves the XVF3800 with `arecord -l`, then opens that current card/device at its verified native `S16_LE`, two-channel, 16 kHz capture format. It does not store card 3 as a fixed setting. Capture reads have a hard deadline, and audio is downmixed to 16 kHz mono for the wake word and STT. Other USB microphones retain the PyAudio fallback.

If `microphone_capture` reports the known immediate ALSA I/O error, stop retrying and reseat USB:

```bash
sudo systemctl stop r08y 2>/dev/null || true
```

Physically unplug the XVF3800 USB cable, wait 10 seconds, reconnect it, and wait another 10 seconds. Then confirm the device has returned and rerun Gate C:

```bash
arecord -l
wpctl status -n
cd /opt/r08y
./venv/bin/python -m r08y --config config.yaml --audio-test
```

### Gate D: ROBY wake-word test

This step starts only microphone capture and the custom wake-word detector:

```bash
cd /opt/r08y
set -a
source /etc/r08y/r08y.env
set +a
./venv/bin/python -m r08y --config config.yaml --wake-word-test
```

Wait for `READY`, say “ROBY” once, then stop talking. The test ends immediately on detection or after 15 seconds. Expected result:

```text
[PASS] wake_word: 'ROBY' detected by the custom model
```

If it reports a missing AccessKey or model, fix that exact item. Do not enable RMS fallback for normal use; it responds to volume rather than the word ROBY.

### Gate E: guided expression test

Stop the service and clear hands and cables from the camera pan/tilt assembly:

```bash
sudo systemctl stop r08y 2>/dev/null || true
cd /opt/r08y
./venv/bin/python -m r08y --config config.yaml --expression-test
```

This command does not initialize mobility. R08Y performs seven bounded gestures, reports each one before it begins, and returns pan/tilt home. Confirm that movement is smooth, remains inside the physical clearance, and does not bind. Press Ctrl-C to cancel if anything catches or strains.

### Gate F: camera, pan/tilt, sensors, microphone, and speaker

Keep the wheels raised:

```bash
./venv/bin/python -m r08y --config config.yaml \
  --smoke-test --exercise-hardware
```

Confirm all of the following:

- a camera frame is captured
- pan/tilt moves to its home position without binding
- ultrasonic distance is plausible
- left and right light-sensor voltages are present
- microphone average RMS is above zero when you speak
- the short 660 Hz tone is clearly audible from the new speaker

The command says not to speak during startup, prints `READY` before its two-second microphone sample, and tells you when the sample is complete. Pressing Ctrl-C exits the diagnostic process immediately if a native camera operation is stuck.

### Gate G: guarded motor exercise

Verify again that every wheel is clear. Then run:

```bash
./venv/bin/python -m r08y --config config.yaml \
  --smoke-test --exercise-hardware \
  --exercise-mobility --confirm-wheels-raised
```

The sequence is short and low speed: strafe right, strafe left, turn left, turn right, forward, backward. The controller stops between patterns and stops again on exit.

Watch each wheel. If a wheel turns opposite the expected direction, stop the test and reverse only that wheel’s entry in `mobility.wheel_scale`. The order is front-left, rear-left, front-right, rear-right. Examples:

```yaml
wheel_scale: [1.0, 1.0, 1.0, 1.0]
wheel_scale: [1.0, -1.0, 1.0, 1.0]  # reverse rear-left only
```

Do not change upstream Freenove pin or channel assignments in R08Y.

## 8. Start the service

```bash
sudo systemctl start r08y
sudo systemctl status r08y --no-pager
journalctl -u r08y -f
```

Expected first-run behavior:

1. Each available component reports ready in the health summary.
2. R08Y says, “R08Y online. Ready when you are.”
3. Saying “ROBY” starts a voice turn.
4. The camera tracks a face through the expression controller.
5. R08Y makes a small acknowledgement after the wake word, holds still while listening, shifts gently while thinking, and adds a restrained speaking bob.
6. “How far?” reports ultrasonic distance.
7. “Check light sensors” reports both sensor voltages.
8. “Move forward” runs briefly only when valid clearance is available.
9. “Stop wheels” sends an immediate zero-duty command.

For the shortest live audio acceptance test, keep a second terminal on the log:

```bash
journalctl -u r08y -f
```

Listen for “R08Y online. Ready when you are.” Say “ROBY” by itself. Pause while R08Y makes its small acknowledgement motion, then say, “Tell me your name in one sentence.” The log should show `Wake word 'ROBY' detected`, a non-empty transcription, and an AI response. The response should play through the XVF3800 speaker.

Useful service controls:

```bash
sudo systemctl restart r08y
sudo systemctl stop r08y
journalctl -u r08y -n 200 --no-pager
```

## 9. Hold the UPS as a separate change

Do not install the UPS HAT during the speaker and full-system acceptance pass. Once camera, voice, sensors, and motors pass on direct USB-C power:

1. Record the clean smoke-test output.
2. Identify the exact UPS HAT model and its shutdown/telemetry interface.
3. Test the HAT without motor movement first.
4. Recheck `vcgencmd get_throttled` under speaker playback and motor load.
5. Only then set `power.ups_enabled: true` and add model-specific telemetry.

## Troubleshooting boundaries

- Missing `params.json`: run the official Freenove parameter tool and enter verified board versions.
- No XVF3800 match: use `arecord -l` and `wpctl status -n`; keep strict matching on and correct the configured name.
- Immediate XVF3800 capture I/O error: stop R08Y, physically unplug/replug XVF3800 USB with the 10-second waits above, then rerun Gate C. Reboots alone may not clear this state.
- No microphone samples without an I/O error: confirm the XVF3800 by name in both `arecord -l` and `wpctl status -n`, then rerun Gate C.
- No sound: verify the speaker is on the documented JST connector and confirm the XVF3800 sink and volume.
- ROBY not armed: confirm `R08Y_PICOVOICE_KEY` is set locally, `R08Y_WAKE_WORD_PATH` points to the installed Raspberry Pi `.ppn`, source the environment for manual tests, and rerun Gate D.
- ROBY test times out: face the array, say only “ROBY” at normal conversational volume, and raise `wake_word.sensitivity` in small steps. Keep the value between `0.0` and `1.0`.
- `cv2` has no attribute `data`: pull the current branch and rerun `bash install.sh`. The installer adds Debian's `opencv-data`, and R08Y now resolves its system cascade path directly. If cascade data is still unavailable, face detection is disabled while voice remains online.
- No camera frame: list `/dev/video*`, then set `camera.device_path` only after identifying the correct node.
- Forward blocked: fix the ultrasonic reading. Do not disable the guard for normal operation.
- Motor direction wrong: correct `wheel_scale` with wheels raised. Do not guess at GPIO or PWM mappings.
