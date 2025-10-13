Holiday Skeleton (PCA9685 + MQTT + Home Assistant)

One-file animatronic skeleton that **listens → thinks → speaks**, with **moving jaw** and **PWM eyes** via PCA9685, offline **Vosk** STT, **Piper** TTS, and optional **Ollama** LLM quips. Auto-publishes **MQTT discovery** so Home Assistant gets clean controls out-of-the-box.

## Hardware (quick)
- **Raspberry Pi** (Bookworm OK)
- **PCA9685** @ `0x40` on I²C-1
- **Servo** (jaw) → PCA9685 **channel 0** (default)  
- **Eyes LEDs** → PCA9685 **channel 4** (default) through a **transistor/MOSFET** + resistor (don’t drive LEDs directly from PCA pin)
- **PIR** → Pi GPIO **17** (3.3V logic); adjust sensitivity on the module trimmer

**I2C check:** `sudo i2cdetect -y 1` should show `40`.

## Wiring Notes
- **Eyes (LEDs):** PCA9685 CH4 → NPN/MOSFET gate. LED anode to +5V through resistor, cathode to MOSFET drain/collector; MOSFET source/emitter to GND. Don’t forget **common ground** between Pi, PCA9685, and LED supply.
- **Servo (Jaw):** PCA9685 CH0 signal → servo signal; provide **adequate 5–6V** and current for servo separately; **common ground** required.
- **PIR:** OUT → GPIO17; VCC → 5V (module-dependent); GND → GND. Tweak sensitivity & retrigger knobs to reduce false triggers.

## Home Assistant
Clean discovery is published to topics under `homeassistant/…` with device name `skeleton` (configurable). Minimal controls only.

### Optional: Type-to-Say
Use `ha/helpers.yaml` (an `input_text`) and `ha/automation_say_from_input_text.yaml` to push text to `holiday/skeleton/say/set`.

### Example Lovelace (Dashboard) YAML
```yaml
title: Skeleton
views:
  - title: Controls
    cards:
      - type: entities
        title: Skeleton Main
        entities:
          - entity: switch.skeleton_motion_enabled
          - entity: switch.skeleton_night_mode
          - entity: number.skeleton_eyes_dim
          - entity: number.skeleton_eyes_full
          - entity: number.skeleton_volume
          - entity: button.skeleton_blink
          - entity: button.skeleton_flicker
          - entity: button.skeleton_say
      - type: entities
        title: Status
        entities:
          - binary_sensor.skeleton_motion
          - binary_sensor.skeleton_speaking
          - sensor.skeleton_status
          - sensor.skeleton_reply_time
          - sensor.skeleton_transcript
```

## Install (short)
1. `sudo apt-get install -y python3-venv portaudio19-dev alsa-utils i2c-tools python3-lgpio`
2. `sudo usermod -aG i2c,audio,video,gpio $USER` → reboot once.
3. Copy code to `/opt/holiday-skeleton`, create venv, `pip install -r requirements.txt`
4. Install `systemd/holiday-skeleton.service` and an override env file (see below).
5. `sudo systemctl enable --now holiday-skeleton`

## Systemd override (env)
Create `/etc/systemd/system/holiday-skeleton.service.d/override.conf`:
```ini
[Service]
Environment="MQTT_HOST=192.168.68.70"
Environment="MQTT_PORT=1883"
Environment="MQTT_USER=<username>"
Environment="MQTT_PASS=Your!Password"   # keep quotes if it contains !
Environment="EYES_INVERT=0"
Environment="EYES_LISTEN_FRAC=0.18"
Environment="EYES_SPEAK_FRAC=1.0"
Environment="EYES_IDLE_FRAC=0.0"
Environment="EYES_CH=4"
Environment="JAW_CH=0"
Environment="JAW_MIN_US=512"
Environment="JAW_MAX_US=1000"
Environment="PIR_PIN=17"
```

## License
MIT
