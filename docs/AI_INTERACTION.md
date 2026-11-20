# Reachy-Style AI Interaction

This guide covers the conversation layer added on the `claude/reachy-style-brain` branch. It brings R08Y closer to how Pollen Robotics' Reachy Mini conversation app feels: the model decides when to move, look, and show emotion, and the body stays alive while it talks.

## What changed

| Reachy Mini conversation app | R08Y now |
|---|---|
| The model calls `move_head`, `play_emotion`, `dance`, `camera`, `head_tracking` | Claude calls `move_head`, `express`, `look_with_camera`, `set_face_tracking` |
| Camera photo sent to the model on demand | `look_with_camera` sends one JPEG when Claude decides to look |
| Head wobble driven by the loudness of the voice being played | Pan/tilt sway and nod follow the measured loudness of each reply |
| Always listening with voice activity detection | ROBY starts a conversation; R08Y then listens for 6 seconds after each reply without needing ROBY again |
| Barge-in cancels speech | Optional: say ROBY mid-reply (`interaction.barge_in`) |
| Motion tools need no spoken follow-up | A gesture-only answer stays silent |
| Weather, time, and other tools | `use_skill` exposes R08Y's existing skills (weather, timers, briefings, and more) |

Added for R08Y's own hardware:

- `drive`: guarded mecanum moves. **Off by default** (`ai.allow_driving: false`) because the ultrasonic sensor sees obstacles, not table edges.
- `read_sensors`: distance, light sensors, and battery.
- `learn_face`: remember the person in view under a name.
- `end_conversation`: stop the follow-up window after a goodbye.

## How a turn works

```text
ROBY ─> cue + nod ─> record ─> STT ─> skill keyword match? ──yes──> skill reply
                                           │ no
                                           v
                            Claude (Sonnet 5.5, effort low)
                               │            ▲
                    tool calls │            │ tool results (photo, sensor text)
                               v            │
       "Let me look." plays ─> RobotTools: express / move_head / look_with_camera /
                               drive / read_sensors / learn_face / use_skill
                                           │
                                           v
        final reply ─> TTS ─> loudness envelope ─> head sway/nod while speaking
                                           │
                                           v
                 listen 6 s for a follow-up (no ROBY) ─> next turn, or back to ROBY
```

Key behaviors:

- **Words before an action play while it runs.** If Claude says "Let me look." and calls the camera tool in the same reply, the phrase plays while the photo is taken and the next request runs.
- **One gesture, not two.** When Claude picks an emotion, the older keyword-based gesture is skipped for that reply.
- **Deliberate looks hold.** After `move_head`, face tracking pauses for 5 seconds so the camera does not snap back to the nearest face.
- **Refusals are short.** If Claude declines, R08Y says "That's one I can't help with." Server-side fallback (`ai.refusal_fallback`) first retries some declines on another Claude model.
- **History stays clean.** Earlier turns are sent as plain text, so photos are not resent every turn. The conversation also always starts with a user turn; before this change, an assistant turn could end up first once a person's history reached the 20-turn limit, and the API rejects that.

## Settings

All new settings have defaults, so an existing `/opt/r08y/config.yaml` keeps working. The installer never overwrites that file, so add the lines below yourself.

```yaml
interaction:
  follow_up_seconds: 6.0   # 0 = ROBY before every turn
  speech_motion: true
  barge_in: false          # enable only after the echo test below

stt:
  fallback_provider: groq  # needs R08Y_GROQ_API_KEY

ai:
  effort: low              # fastest replies; medium thinks more first
  tools_enabled: true
  allow_driving: false     # true only with R08Y on the floor
  max_tool_rounds: 4
  refusal_fallback: true

tts:
  fallback_provider: edge  # speaks if Hugging Face cannot

power:                     # after the UPS checks in HOWTO section 9
  ups_enabled: true
  ups_model: waveshare_ups_hat_d
```

`requirements.txt` now pins `anthropic>=1.11,<2`. Run `bash install.sh` again after pulling so the Pi gets an SDK that supports `output_config` and server-side fallback.

## About the Hugging Face voice

The Hugging Face realtime STT and TTS path uses Pollen's session service for Reachy Mini (`pollen-robotics-reachy-mini-realtime-url.hf.space`). It is undocumented, intended for Reachy Mini owners, and users have reported recent `verified_hf_token_required` rejections. Treat it as a bonus, not a dependency:

- With `tts.fallback_provider: edge`, R08Y still speaks when Hugging Face rejects the token at startup or fails mid-reply.
- With `stt.fallback_provider: groq` and a Groq key, transcription continues when Hugging Face fails. Silence never triggers the fallback.
- For a voice you control end to end, set `tts.provider: edge` and `stt.provider: groq`.

## Test sequence on the Pi

Run these with the service stopped, after `bash install.sh`:

```bash
cd /opt/r08y
set -a; source /etc/r08y/r08y.env; set +a
./venv/bin/python -m r08y --config config.yaml --check-config
./venv/bin/python -m r08y --config config.yaml --chat-test
```

`--chat-test` lets you type to Claude while the camera, pan/tilt, and sensors are live. Wheels and audio never start. Try:

1. "What do you see?" — the log shows `Claude tool: look_with_camera`, then a description.
2. "What color is the thing in front of you?" — if red and blue come back swapped, the Picamera2 color conversion in `camera.py` needs flipping (see Known limits).
3. "Look to your left." — the head turns and holds.
4. "That's great news!" — expect `express` with a happy or excited gesture.
5. "How far is the wall?" — expect `read_sensors`.
6. "Bye for now." — expect `end_conversation`.

Then start the service and talk to it:

1. "ROBY" … "What do you see?" — R08Y should say a short line, look, then answer.
2. Without saying ROBY again, within 6 seconds: "What color is it?" — this tests the follow-up window.
3. Stay quiet for 6 seconds — the log shows `Follow-up window closed`.
4. Watch the head while R08Y talks: louder words should lift and sway it more than pauses.

### Echo test before enabling barge-in

Barge-in listens for ROBY while R08Y talks, so the XVF3800 echo cancellation must keep R08Y's own voice out of the wake word. Enable `interaction.barge_in: true`, then ask "What's your name?" several times. If R08Y ever cuts itself off when it says its own name, turn barge-in back off.

### Follow-up sensitivity

Follow-up listening opens when two chunks in a row exceed `audio.rms_threshold` (500). A TV or fan can open the window; R08Y then returns to waiting for ROBY without answering. If that happens often, raise `rms_threshold` or set `follow_up_seconds: 0`.

## UPS HAT (D) power notes

The Waveshare UPS HAT (D) holds two 21700 cells in parallel. It reports them through an INA219 at I2C `0x43` and has a power-management MCU at `0x2D`. The Freenove board uses `0x40` (PCA9685) and `0x48` (ADC), so there is no address conflict. R08Y only reads the INA219. It never writes to `0x2D`, so it cannot cut power or arm auto-boot.

**Check the power path before you connect both packs.** The Freenove car board feeds the Pi's 5 V rail from its 18650 cells through the header when switch S1 is on. The UPS HAT (D) feeds the Pi from underneath through pogo pins. Neither vendor documents running both sources into the same 5 V rail, and neither documents backfeed protection. Before connecting both:

1. Confirm with Waveshare's wiki which Pi pads the pogo pins touch.
2. Decide which pack powers the Pi. A common split is UPS for the Pi and USB audio, Freenove cells for motors and servos only.
3. With a multimeter, confirm the Freenove board does not also drive the Pi's 5 V pins in that configuration.
4. Charge the UPS through its own USB-C port, not the Pi's.

Then follow HOWTO section 9.

## Notes for the 3D-printed body

- **Microphones:** keep the XVF3800's four mics open to the room. Add a grille over the array instead of a solid panel, and keep plastic at least a few millimeters off the mic ports.
- **Speaker:** give the speaker its own sealed chamber, mounted on rubber or foam, facing away from the array. Echo cancellation works best when little sound travels through the shell into the mics; this also matters for barge-in.
- **Pan/tilt clearance:** the gestures use up to ±30° pan and about ±20° tilt around 90°. Check the full servo range (30–150° in config) against the shell before printing the final version.
- **Camera and ultrasonic:** leave both unobstructed. Forward driving depends on clean ultrasonic readings.
- **Heat:** the Pi 4 throttles when hot. Vent over the SoC and keep `vcgencmd get_throttled` at `0x0` under load.
- **Batteries:** mount the two 21700 cells low and centered for stability. Leave the UPS USB-C charge port, switches, and the Freenove power switches reachable.

## Known limits

These are written and unit-tested but not yet run on the robot:

- Camera colors in photos sent to Claude. Picamera2's `RGB888` format may already be in OpenCV's BGR order, in which case `camera.py`'s extra RGB-to-BGR conversion swaps red and blue. The color question in the chat test checks this.
- Speech motion timing on real servos. The envelope starts when playback starts, so `pw-play` startup delay can put motion a few tens of milliseconds early.
- Barge-in, which depends on XVF3800 echo cancellation.
- UPS HAT readings, which follow Waveshare's sample code. Compare one reading with a multimeter.
