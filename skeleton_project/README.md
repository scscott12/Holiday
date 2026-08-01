# Holiday Skeleton (PCA9685 + MQTT + Home Assistant)

Single-process animatronic skeleton that **listens → thinks → speaks**, with **moving jaw** and **PWM eyes** via PCA9685, offline **Vosk** STT, **Piper** TTS, and optional **Ollama** LLM quips. Auto-publishes **MQTT discovery** so Home Assistant gets clean controls out-of-the-box.

The service uses a serialized event controller: MQTT and PIR callbacks only enqueue work, while one controller owns speech, listening, eyes, and jaw movement. This prevents overlapping conversations and hardware races without adding inter-process latency.

## Runtime structure

```text
skeleton_all_in_one_mqtt.py  service composition and hardware integrations
holiday_skeleton/
  controller.py              event queue and runtime states
  audio.py                   preroll, speech gate, and endpoint timing
  brain.py                   Ollama stream producer and phrase assembly
  speech.py                  warm Piper voice, PCM playback, and jaw envelope
  discovery.py               shared Home Assistant MQTT definitions
tests/                       hardware-free unit tests
```

Runtime states published to `holiday/skeleton/status` are `starting`, `idle`, `greeting`, `listening`, `thinking`, `speaking`, `effect`, `cooldown`, `stopping`, and `error`.

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

### Type-to-Say

MQTT discovery creates `text.skeleton_say`. Entering text there publishes it to `holiday/skeleton/say/set`. The files under `ha/` remain available if you prefer an `input_text` plus button workflow.

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
          - entity: text.skeleton_say
      - type: entities
        title: Status
        entities:
          - binary_sensor.skeleton_motion
          - binary_sensor.skeleton_speaking
          - sensor.skeleton_status
          - sensor.skeleton_reply_time
          - sensor.skeleton_llm_first_token
          - sensor.skeleton_llm_first_phrase
          - sensor.skeleton_response_first_audio
          - sensor.skeleton_tts_engine
          - sensor.skeleton_tts_first_audio
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
Environment="SPEECH_START_TIMEOUT=10.0"
Environment="END_SILENCE_SEC=0.75"
Environment="MAX_UTTERANCE_SEC=12.0"
Environment="AUDIO_OUTPUT_DEVICE="
Environment="TTS_FRAME_MS=20"
Environment="LLM_PHRASE_MIN_CHARS=12"
Environment="LLM_PHRASE_SOFT_CHARS=36"
Environment="LLM_PHRASE_MAX_CHARS=72"
```

Speech timing is split into three controls:

- `SPEECH_START_TIMEOUT`: how long the visitor has to begin talking.
- `END_SILENCE_SEC`: how much silence ends an utterance.
- `MAX_UTTERANCE_SEC`: maximum speech length after talking begins.

The default 0.75-second endpoint silence is a good starting point for a responsive outdoor prop. Raise `ENERGY_GATE` if ambient noise starts false conversations.

## Low-latency speech

The service loads `PIPER_MODEL` once during startup, runs one silent inference to warm the ONNX path, and keeps a PortAudio output stream ready. Speech is sent directly from Piper to the speaker as signed 16-bit PCM, so the normal path no longer launches Piper for every line, writes `/tmp/tts.wav`, reopens it, or scans the whole WAV before playback.

The jaw follows 20 ms RMS audio frames as those same frames are written to the speaker. Change `TTS_FRAME_MS` only if the servo needs slower movement; 15–25 ms is the useful range. `AUDIO_OUTPUT_DEVICE` may be a sounddevice device index or a unique device-name substring. Leave it empty to use the system default.

Home Assistant reports:

- `sensor.skeleton_tts_engine`: `streaming` on the warm path or `legacy` when startup falls back to the existing Piper binary.
- `sensor.skeleton_tts_model_load_time`: one-time voice load duration.
- `sensor.skeleton_tts_warmup_time`: one-time silent inference that removes first-greeting cold start.
- `sensor.skeleton_tts_first_audio`: synthesis-to-first-PCM latency for the latest utterance.
- `sensor.skeleton_tts_speak_time`: total synthesis and playback time.
- `sensor.skeleton_tts_audio_time`: generated PCM duration for comparison with wall time.

After upgrading an existing Pi checkout, reinstall requirements before restarting the service:

```bash
sudo /opt/holiday-skeleton/venv/bin/pip install -r /opt/holiday-skeleton/requirements.txt
sudo systemctl restart holiday-skeleton
sudo journalctl -u holiday-skeleton -f
```

Startup should log `Piper voice warm and output stream ready`. If it logs `using legacy Piper process`, verify the `piper-tts` install, `PIPER_MODEL`, its adjacent `.onnx.json` file, and the configured output device.

## Low-latency replies

Ollama now returns newline-delimited streaming chunks. A background producer keeps reading those chunks while the controller speaks completed clauses through the warm Piper engine. This overlaps the remaining LLM generation with audio playback without giving a background thread access to the eyes, jaw, microphone, or speaker.

Phrase boundaries prefer sentence punctuation, then commas/semicolons after `LLM_PHRASE_SOFT_CHARS`, and finally a word boundary at `LLM_PHRASE_MAX_CHARS`. The defaults are intentionally conservative so speech sounds natural. Lower the soft/max values slightly if first audio is still slow; raise them if the voice sounds too fragmented. Keep `MIN <= SOFT <= MAX`.

Home Assistant reports each layer separately:

- `sensor.skeleton_llm_first_token`: request to Ollama's first non-empty text chunk.
- `sensor.skeleton_llm_first_phrase`: request to the first speakable phrase.
- `sensor.skeleton_response_first_audio`: request to the first PCM frame written to the speaker.
- `sensor.skeleton_reply_time`: total Ollama generation time, which can finish while Piper is already speaking.
- `sensor.skeleton_tts_first_audio`: first-phrase Piper synthesis latency only.

The full generated response is retained for the transcript even though it is spoken in several phrases. If Ollama fails before producing a phrase, the skeleton speaks its local fallback line.

## Checks

Run the hardware-free checks from `skeleton_project/`:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
```

## License
[MIT](LICENSE)
