# Holiday Skeleton (PCA9685 + MQTT + Home Assistant)

Single-process animatronic skeleton that **listens → thinks → speaks**, with **moving jaw**, **PWM eyes**, hot-swappable personality packs, and configurable multi-step scenes via PCA9685, offline **Vosk** STT, **Piper** TTS, and optional **Ollama** LLM quips. Auto-publishes **MQTT discovery** so Home Assistant gets clean controls out-of-the-box.

The service uses a serialized event controller: MQTT and PIR callbacks only enqueue work, while one controller owns speech, listening, eyes, and jaw movement. This prevents overlapping conversations and hardware races without adding inter-process latency.

## Runtime structure

```text
skeleton_all_in_one_mqtt.py  service composition and hardware integrations
holiday_skeleton/
  controller.py              event queue and runtime states
  audio.py                   preroll, speech gate, and endpoint timing
  barge_in.py                echo-aware interruption command monitor
  brain.py                   Ollama stream producer and phrase assembly
  content.py                 transactional personality/scene preparation
  health.py                  health aggregation and Pi telemetry
  idle_life.py               sparse idle-action scheduler
  personality.py             validated character packs and bounded settings
  scene.py                   validated scene files, cue loading, and bounded runner
  settings.py                atomic non-sensitive operator-state persistence
  self_test.py               bounded manual output verification
  speech.py                  warm Piper voice, PCM playback, and jaw envelope
  watchdog.py                native systemd readiness and hang recovery
  discovery.py               shared Home Assistant MQTT definitions
tests/                       hardware-free unit tests
```

Runtime states published to `holiday/skeleton/status` are `starting`, `idle`, `maintenance`, `idle_life`, `scene`, `self_test`, `content_reload`, `greeting`, `listening`, `thinking`, `speaking`, `effect`, `cooldown`, `stopping`, and `error`.

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
          - entity: switch.skeleton_maintenance_mode
          - entity: switch.skeleton_motion_enabled
          - entity: switch.skeleton_idle_life_enabled
          - entity: switch.skeleton_night_mode
          - entity: number.skeleton_eyes_dim
          - entity: number.skeleton_eyes_full
          - entity: number.skeleton_volume
          - entity: select.skeleton_personality
          - entity: button.skeleton_play_personality_scene
          - entity: button.skeleton_run_self_test
          - entity: button.skeleton_stop_self_test
          - entity: button.skeleton_reload_content
          - entity: button.skeleton_blink
          - entity: button.skeleton_flicker
          - entity: text.skeleton_say
          - entity: text.skeleton_play_scene
          - entity: button.skeleton_stop_scene
      - type: entities
        title: Status
        entities:
          - binary_sensor.skeleton_motion
          - binary_sensor.skeleton_speaking
          - binary_sensor.skeleton_idle_life_active
          - sensor.skeleton_idle_life_state
          - sensor.skeleton_idle_life_last_action
          - binary_sensor.skeleton_scene_active
          - sensor.skeleton_scene_state
          - sensor.skeleton_current_scene
          - sensor.skeleton_scene_step
          - sensor.skeleton_personality_state
          - sensor.skeleton_personality_default_scene
          - sensor.skeleton_saved_settings_state
          - sensor.skeleton_settings_last_saved
          - sensor.skeleton_settings_last_error
          - sensor.skeleton_maintenance_state
          - sensor.skeleton_maintenance_last_result
          - sensor.skeleton_maintenance_last_error
          - sensor.skeleton_maintenance_blocked_commands
          - binary_sensor.skeleton_self_test_active
          - sensor.skeleton_self_test_state
          - sensor.skeleton_self_test_step
          - sensor.skeleton_self_test_last_result
          - sensor.skeleton_self_test_last_error
          - binary_sensor.skeleton_content_reload_active
          - sensor.skeleton_content_reload_state
          - sensor.skeleton_content_reload_last_result
          - sensor.skeleton_content_reload_last_error
          - sensor.skeleton_status
          - sensor.skeleton_reply_time
          - sensor.skeleton_llm_first_token
          - sensor.skeleton_llm_first_phrase
          - sensor.skeleton_response_first_audio
          - sensor.skeleton_memory_turns
          - sensor.skeleton_tts_engine
          - sensor.skeleton_tts_cache_state
          - sensor.skeleton_tts_cached_lines
          - binary_sensor.skeleton_tts_cache_hit
          - sensor.skeleton_tts_first_audio
          - sensor.skeleton_greeting_first_audio
          - sensor.skeleton_transcript
      - type: entities
        title: Health & Performance
        entities:
          - binary_sensor.skeleton_health_ok
          - sensor.skeleton_health
          - sensor.skeleton_health_reasons
          - sensor.skeleton_cpu_temperature
          - sensor.skeleton_memory_use
          - sensor.skeleton_disk_use
          - binary_sensor.skeleton_pi_throttled
          - sensor.skeleton_response_first_audio_rolling_p95
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
Environment="MQTT_HOST=<broker-ip>"
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
Environment="IDLE_LIFE_ENABLED=1"
Environment="IDLE_LIFE_MIN_SEC=18.0"
Environment="IDLE_LIFE_MAX_SEC=45.0"
Environment="IDLE_MUTTER_CHANCE=0.12"
Environment="IDLE_EYE_PULSE_FRAC=0.10"
Environment="IDLE_JAW_TWITCH_FRAC=0.14"
Environment="SCENES_ENABLED=1"
Environment="SCENES_PATH=/opt/holiday-skeleton/scenes.json"
Environment="SCENE_SOUND_DIR=/opt/holiday-skeleton/sounds"
Environment="SCENE_MAX_SECONDS=30"
Environment="PERSONALITIES_ENABLED=1"
Environment="PERSONALITIES_PATH=/opt/holiday-skeleton/personalities.json"
Environment="PERSONALITY=pirate"
Environment="PERSIST_SETTINGS_ENABLED=1"
Environment="PERSIST_SETTINGS_PATH=/var/lib/holiday-skeleton/operator-settings.json"
Environment="MAINTENANCE_MODE=0"
Environment="HEALTH_INTERVAL_SEC=30"
Environment="HEALTH_LATENCY_WINDOW=20"
Environment="HEALTH_TEMP_WARN_C=75"
Environment="HEALTH_TEMP_CRITICAL_C=82"
Environment="OLLAMA_HEALTHCHECK_ENABLED=1"
Environment="SELF_TEST_ENABLED=1"
Environment="SELF_TEST_MAX_SECONDS=12"
Environment="SELF_TEST_EYES_FRAC=0.25"
Environment="SELF_TEST_JAW_FRAC=0.20"
Environment="SELF_TEST_STEP_SEC=0.35"
Environment="SELF_TEST_LINE=Systems awake and ready."
Environment="SPEECH_START_TIMEOUT=10.0"
Environment="END_SILENCE_SEC=0.75"
Environment="MAX_UTTERANCE_SEC=12.0"
Environment="BARGE_IN_ENABLED=1"
Environment="BARGE_IN_STOP_COMMANDS=stop,quiet"
Environment="BARGE_IN_LISTEN_COMMANDS=wait"
Environment="BARGE_IN_WAKE_WORDS=skeleton"
Environment="BARGE_IN_ENERGY_GATE=320"
Environment="BARGE_IN_REQUIRE_WAKE_WORD=0"
Environment="AUDIO_OUTPUT_DEVICE="
Environment="TTS_FRAME_MS=20"
Environment="TTS_CANNED_CACHE=1"
Environment="LLM_PHRASE_MIN_CHARS=12"
Environment="LLM_PHRASE_SOFT_CHARS=36"
Environment="LLM_PHRASE_MAX_CHARS=72"
Environment="LLM_MEMORY_TURNS=3"
Environment="LLM_CONTEXT_TOKENS=512"
```

Speech timing is split into three controls:

- `SPEECH_START_TIMEOUT`: how long the visitor has to begin talking.
- `END_SILENCE_SEC`: how much silence ends an utterance.
- `MAX_UTTERANCE_SEC`: maximum speech length after talking begins.

The default 0.75-second endpoint silence is a good starting point for a responsive outdoor prop. Raise `ENERGY_GATE` if ambient noise starts false conversations.

## Low-latency speech

The service loads `PIPER_MODEL` once during startup, runs one silent inference to warm the ONNX path, and keeps a PortAudio output stream ready. Speech is sent directly from Piper to the speaker as signed 16-bit PCM, so the normal path no longer launches Piper for every line, writes `/tmp/tts.wav`, reopens it, or scans the whole WAV before playback.

The jaw follows 20 ms RMS audio frames as those same frames are written to the speaker. Change `TTS_FRAME_MS` only if the servo needs slower movement; 15–25 ms is the useful range. `AUDIO_OUTPUT_DEVICE` may be a sounddevice device index or a unique device-name substring. Leave it empty to use the system default.

With `TTS_CANNED_CACHE=1` (the default), every morning, afternoon, evening, night, goodbye, and idle-mutter line in the active personality plus every scene-speech line is synthesized silently during service startup. The raw PCM and jaw envelope stay in memory, so a motion greeting, goodbye, idle mutter, or scene line can write its first frame immediately without invoking Piper again. Dynamic Home Assistant text and Ollama phrases still use live streaming synthesis. A live personality switch pre-renders the incoming pack before activation and prunes lines used only by the previous pack, preventing repeated switches from growing RAM indefinitely. Cache keys normalize whitespace, and the cache belongs only to the currently loaded voice instance. Set `TTS_CANNED_CACHE=0` if startup time matters more than instant canned lines.

Home Assistant reports:

- `sensor.skeleton_tts_engine`: `streaming` on the warm path or `legacy` when startup falls back to the existing Piper binary.
- `sensor.skeleton_tts_model_load_time`: one-time voice load duration.
- `sensor.skeleton_tts_warmup_time`: one-time silent inference that removes first-greeting cold start.
- `sensor.skeleton_tts_cache_state`: `warming`, `ready`, `partial`, `disabled`, or `legacy`.
- `sensor.skeleton_tts_cached_lines`: number of opening, goodbye, idle, and scene lines held in memory.
- `sensor.skeleton_tts_cache_warmup_time` and `sensor.skeleton_tts_cache_memory`: startup cost and RAM used by cached PCM.
- `binary_sensor.skeleton_tts_cache_hit`: whether the latest streaming-engine utterance used cached audio.
- `sensor.skeleton_tts_first_audio`: synthesis-to-first-PCM latency for the latest utterance.
- `sensor.skeleton_greeting_first_audio`: greeting-call to first-PCM latency, including cache lookup and playback setup.
- `sensor.skeleton_tts_speak_time`: total synthesis and playback time.
- `sensor.skeleton_tts_audio_time`: generated PCM duration for comparison with wall time.

After upgrading an existing Pi checkout, reinstall requirements before restarting the service:

```bash
sudo /opt/holiday-skeleton/venv/bin/pip install -r /opt/holiday-skeleton/requirements.txt
sudo systemctl restart holiday-skeleton
sudo journalctl -u holiday-skeleton -f
```

Startup should log both `Piper voice warm and output stream ready` and a `[TTS cache] ... canned lines ready` summary. If it logs `using legacy Piper process`, verify the `piper-tts` install, `PIPER_MODEL`, its adjacent `.onnx.json` file, and the configured output device.

## Low-latency replies

Ollama now returns newline-delimited streaming chunks. A background producer keeps reading those chunks while the controller speaks completed clauses through the warm Piper engine. This overlaps the remaining LLM generation with audio playback without giving a background thread access to the eyes, jaw, microphone, or speaker.

Phrase boundaries prefer sentence punctuation, then commas/semicolons after the configured soft boundary, and finally a word boundary at the maximum. The defaults are intentionally conservative so speech sounds natural. With personality packs enabled, tune `reply.phrase_minimum`, `phrase_soft`, and `phrase_maximum` in each pack; the `LLM_PHRASE_*` environment values remain the legacy fallback. Lower soft/max slightly if first audio is still slow, raise them if the voice sounds fragmented, and keep `minimum <= soft <= maximum`.

Home Assistant reports each layer separately:

- `sensor.skeleton_llm_first_token`: request to Ollama's first non-empty text chunk.
- `sensor.skeleton_llm_first_phrase`: request to the first speakable phrase.
- `sensor.skeleton_response_first_audio`: request to the first PCM frame written to the speaker.
- `sensor.skeleton_reply_time`: total Ollama generation time, which can finish while Piper is already speaking.
- `sensor.skeleton_tts_first_audio`: first-phrase Piper synthesis latency only.

The full generated response is retained for the transcript even though it is spoken in several phrases. If Ollama fails before producing a phrase, the skeleton speaks its local fallback line.

## Short-term conversation memory

Ollama replies now use `/api/chat`. During one motion-triggered visit, each request includes the skeleton's system prompt, its opening line, and up to the latest `LLM_MEMORY_TURNS` completed visitor/skeleton exchanges. This lets a visitor naturally ask follow-ups such as “what do you mean?” without adding a database or another service.

The default pirate pack uses three exchanges with a 512-token context window. Change `reply.memory_turns` and `reply.context_tokens` per pack; `LLM_MEMORY_TURNS` and `LLM_CONTEXT_TOKENS` are used only in legacy mode. More context consumes additional Pi memory and prompt-processing time, so three short turns is the recommended balance for this prop.

Only successful, uninterrupted Ollama replies enter memory. The entire session is cleared on goodbye, listening timeout, shutdown, or completion of the visit; it is never written to disk or shared with the next visitor. Home Assistant's `sensor.skeleton_memory_turns` shows the number of retained exchanges during the active visit and returns to zero when it ends.

`OLLAMA_CHAT_URL` may override the default `http://127.0.0.1:11434/api/chat`. Existing `OLLAMA_URL` settings ending in `/api/generate` are translated to `/api/chat` automatically for upgrade compatibility.

## Barge-in commands

While the streaming Piper engine is speaking, a second command-only Vosk recognizer listens for a small constrained grammar. It does not send arbitrary background speech to Ollama:

- `stop` or `quiet` interrupts within one PCM frame and ends the current visit without another goodbye.
- `wait` or the configured wake name interrupts speech and returns immediately to normal visitor listening.
- `skeleton stop`, `hey skeleton`, and polite variants are included automatically.

The recognizer requires the same command in two consecutive partial results by default, uses a higher energy gate than normal conversation, and ignores a command when that exact wording appears in the phrase currently coming from the speaker. These safeguards reduce self-echo false triggers without adding a cloud service or a second speech model.

Set `BARGE_IN_ENABLED=0` to disable the feature. Each personality supplies its command lists, wake words, and wake-required mode; the comma-separated `BARGE_IN_*` values are the legacy fallback. If nearby speaker audio causes false interruptions, first raise `BARGE_IN_ENERGY_GATE` in increments of 50. Requiring a wake word rejects bare commands and accepts forms such as `skeleton stop`; the wake name by itself still returns to listening.

Barge-in requires the warm streaming Piper path because the legacy WAV player cannot be stopped safely mid-file. Home Assistant reports `ready`, `listening`, `disabled`, `legacy_tts`, `no_microphone`, or `error`, along with the last command, selected action, detection latency, and count. A microphone/full-duplex audio error affects only barge-in; normal speech continues and the next utterance retries the monitor.

## Idle life

When both motion and idle life are enabled, the controller schedules a sparse behavior after 18–45 seconds of uninterrupted idle time. The PIR must be clear for that whole quiet interval. It chooses a short eye pulse or small jaw twitch, with a 12% chance of speaking one configured `IDLE_LINES` mutter. The default cadence makes physical movement noticeable without turning the prop into constant background noise; a mutter occurs roughly every few minutes on average.

Idle life is deliberately lower priority than every visitor or Home Assistant action. PIR activity interrupts it immediately, even before the motion hold timer confirms a full visit. Any queued MQTT command, service shutdown, or barge-in command does the same. Cached Piper mutters stop between the existing 20 ms PCM frames, the jaw returns to rest, and the foreground event then runs on the same serialized controller. When only the legacy Piper binary is available, mutters become jaw twitches because legacy WAV playback cannot be interrupted safely.

Configuration:

- `IDLE_LIFE_ENABLED`: first-run default; the Home Assistant switch persists across restarts when saved settings are enabled.
- `IDLE_LIFE_MIN_SEC` / `IDLE_LIFE_MAX_SEC`: random quiet interval between behaviors.
- `IDLE_MUTTER_CHANCE`: probability from `0.0` to `1.0` that a due action speaks.
- `IDLE_EYE_PULSE_FRAC` / `IDLE_EYE_PULSE_MS`: brightness and duration of the subtle eye pulse. Night mode caps it at the dim-eye level.
- `IDLE_JAW_TWITCH_FRAC` / `IDLE_JAW_TWITCH_MS`: portion of available jaw travel and hold duration.
- `idle_lines` in the active personality: canned mutters that join the TTS cache. `IDLE_LINES` in `prompts.json` is the legacy fallback.

Turning off `switch.skeleton_motion_enabled` disarms idle life as well as visitor triggering. `switch.skeleton_idle_life_enabled` disables only ambient behaviors. Home Assistant reports `ready`, `running`, `disabled`, `disarmed`, or `stopping`, plus the latest action, total action count, and number interrupted by higher-priority activity.

## Personality packs

`personalities.json` packages the settings that make one character feel internally consistent:

- system prompt, time-of-day greetings, goodbyes, idle mutters, and local fallback line;
- memory/context size, maximum reply length, sampling settings, and phrase boundaries;
- spoken-volume multiplier layered on the current Home Assistant/night-mode volume;
- stop, listen, and wake-word grammar for command-only barge-in;
- one default scene available through the **Play Personality Scene** button.

The packaged library includes `pirate`, `graveyard_host`, and `silent_watcher`. Set `PERSONALITY` for the first-run selection or use `select.skeleton_personality` while the service is running; once operator persistence has saved a selection, that value wins across restarts. Switching requires no restart, but it is accepted only while the controller is `idle`, `cooldown`, or safely locked in `maintenance`; a request during a greeting, visit, response, scene, or idle behavior returns `busy` and leaves the active pack untouched.

An accepted switch prepares the incoming Ollama client, barge-in grammar, idle scheduler, and canned-speech cache before changing the active pack. Conversation memory is already empty at this boundary and is explicitly reported as zero. Invalid settings, unknown pack names, unsafe scene names, and missing named scenes cannot partially replace a running personality during a live switch. Startup cross-checks every pack's default scene and reports any mismatch as degraded configuration. If the personality file cannot load at startup, the legacy built-in prompt and optional `prompts.json` override remain available while health reports the configuration error.

Files are limited to 12 packs. Prompts, line counts and lengths, memory/context values, sampling ranges, phrase boundaries, volume multipliers, command lists, and scene identifiers are all bounded during loading. Edit `personalities.json` and press **Reload Content** while idle to load the changed library; changing between already-loaded packs remains immediate. With packs enabled, their settings take precedence over the legacy `SYSTEM_PROMPT`, `IDLE_LINES`, `LLM_*`, and `BARGE_IN_*` prompt/tuning values.

Home Assistant exposes the active pack, readiness, library metadata, default scene, switch count, and last result/error. Library attributes intentionally omit full prompts and canned lines.

## Persistent operator settings

With `PERSIST_SETTINGS_ENABLED=1` (the default), Home Assistant changes survive service restarts and Raspberry Pi reboots. The versioned file stores only:

- the active personality;
- motion and idle-life enable switches;
- night-mode state;
- maintenance lockout state;
- current eye levels and volume; and
- the daytime eye/volume profile needed to leave night mode correctly.

Visitor audio, recognized text, transcripts, conversation memory, prompts, broker details, passwords, and other credentials are never written to this file. Conversation memory remains visit-scoped and RAM-only.

The controller writes a complete temporary JSON document with `0600` permissions, flushes it to disk, then atomically replaces `/var/lib/holiday-skeleton/operator-settings.json`. The packaged systemd service creates `/var/lib/holiday-skeleton` for the service account. A failed write leaves the previous document intact; a missing, malformed, oversized, incompatible, or out-of-range document is ignored and reported as a degraded `settings` component while configured environment defaults continue to run.

Saved operator values take precedence over their corresponding environment defaults after the first successful Home Assistant change. `sensor.skeleton_saved_settings_state` reports `empty`, `restored`, `saved`, `disabled`, or `error`; the last-save and last-error sensors provide deployment diagnostics. To return to environment defaults, stop the service, move the JSON file to a backup name, and start the service again.

Version-1 settings files are accepted and migrated with maintenance mode safely defaulted to off. New saves use version 2.

## Maintenance lockout

Turn on `switch.skeleton_maintenance_mode` before working on the hanging prop, servo linkage, LEDs, amplifier, or speaker. The request has priority over ordinary queued commands and raises a safety interlock immediately; streaming audio, legacy Piper/aplay, listening, LLM generation, scenes, idle actions, self-test, and eye effects all yield. Every eye write is forced off and every jaw write is forced to the configured rest position until the controller completes the lock transition.

While locked, PIR activity is still reported but cannot start a visit. Home Assistant speech, scene, blink, flicker, manual-motion, personality-scene, and self-test commands are rejected instead of being delayed until unlock. Brightness, volume, motion/idle enable values, night mode, personality selection, transactional content reload, restart, and unlock remain available because they do not intentionally actuate the prop; brightness changes are saved but the physical eyes remain off.

The lockout persists through normal service and Pi restarts when operator persistence is enabled. If the state file cannot be written, the live lock remains active and Home Assistant reports `locked_unsaved`; a later restart then uses the configured `MAINTENANCE_MODE` default. Invalid MQTT values cannot unlock the prop. Home Assistant also exposes lock state, last result/error, state-change time, and rejected-command count. Maintenance mode is an operating interlock, not an electrical disconnect—remove power before placing hands in a mechanism that could be energized by wiring faults or independent hardware.

## Scene engine

`scenes.json` defines reusable sequences without adding Python code or another hardware-owning thread. The included `awakening`, `warning`, and `silent_scare` scenes demonstrate coordinated speech, pauses, fixed eye levels, flicker, blink, and jaw movement. Enter a scene name in `text.skeleton_play_scene`, or publish it directly:

```bash
mosquitto_pub -h <broker-ip> -t holiday/skeleton/scene/play/set -m awakening
mosquitto_pub -h <broker-ip> -t holiday/skeleton/scene/stop/set -m PRESS
```

Supported step shapes are:

```json
{"action": "speak", "text": "Careful where you step."}
{"action": "pause", "duration_ms": 250}
{"action": "eyes", "level": 0.6, "duration_ms": 300}
{"action": "blink", "count": 3, "period_ms": 140, "low": 0.0, "high": 0.8}
{"action": "flicker", "duration": 1.2, "base": 0.05, "span": 0.7, "step_ms": 60}
{"action": "jaw", "level": 0.4, "duration_ms": 180}
{"action": "sound", "file": "chains/rattle.wav", "volume": 0.8, "jaw": true}
```

Durations use seconds unless the key ends in `_ms`. Eye levels, jaw levels, and flicker bounds are `0.0`–`1.0`; sound volume is a multiplier from `0.0`–`2.0`. Sound files must be uncompressed 16-bit PCM WAV files below `SCENE_SOUND_DIR`. Relative subfolders are allowed, but absolute paths and `..` traversal are rejected. With streaming Piper, cues are decoded, resampled, and held in memory during startup, then played through the same warm output stream. Set `jaw` to `true` to drive the jaw from the cue envelope. The legacy `aplay` sound-cue path remains interruptible but does not apply the per-cue volume multiplier. Speech steps require streaming Piper because the legacy synthesized-WAV path cannot safely yield mid-line; animation-only scenes still work when the service reports legacy TTS.

Scene files are limited to 32 scenes, 64 steps per scene, 500 characters per speech step, 30 seconds per individual timed step, and `SCENE_MAX_SECONDS` for the whole sequence. Unknown actions, unsafe sound paths, invalid ranges, and malformed JSON are rejected without stopping the core visitor interaction.

Scenes are lower priority than live activity. PIR detection interrupts immediately before its hold timer finishes; any incoming MQTT command interrupts and queues behind the scene; `stop`, `wait`, or the wake name interrupts speech and sound cues through barge-in; shutdown stops between the existing 20 ms audio frames. The controller always rests the jaw and restores idle eyes afterward. Night mode caps scene brightness at the reduced full-eye level, and current global volume still applies.

Home Assistant exposes scene readiness, available names, active scene, current step, run count, interruption count, last result, duration, and last error. Edit `scenes.json` or replace a WAV, then press **Reload Content** while idle so validation, speech caching, and sound preloading run again. Set `SCENES_ENABLED=0` to disable the engine while leaving conversation and manual controls unchanged.

## Live content reload

The **Reload Content** Home Assistant button replaces `personalities.json`, `scenes.json`, and referenced scene WAVs without restarting the service. The request is accepted only while the serialized controller is `idle`, `cooldown`, or locked in `maintenance`; visits, speech, scenes, self-tests, and idle actions return `busy` and continue undisturbed.

Reload is transactional. The runtime first loads both enabled JSON libraries into new immutable objects, requires the currently active personality to still exist, validates every personality default-scene reference, reads every referenced WAV within `SCENE_SOUND_DIR`, rebuilds the active Ollama/barge-in/idle configuration, and pre-renders all required canned speech when the streaming engine and cache are enabled. Reload JSON is capped at 1 MiB per file, decoded scene audio at 64 MiB total, and canned speech at 256 unique lines/64 KiB of text to prevent an accidental Raspberry Pi memory or warmup spike. Only after every step succeeds does the controller swap all active references together. A malformed file, missing cue, invalid cross-reference, oversized candidate, or speech-cache failure leaves the last known-good libraries and visitor behavior unchanged.

Outside maintenance lockout, PIR activity, a later queued MQTT command, or shutdown can interrupt preparation before the commit. While maintenance is locked, PIR remains observable but is intentionally suppressed so hands-on movement does not cancel a safe content update. New canned cache entries are pruned and the active content remains unchanged after any interruption. Home Assistant reports `ready`, `queued`, `reloading`, `error`, `disabled`, or `stopping`, plus last result/error/time/duration, completed attempt count, and interruption count. A failed reload degrades only the `content_reload` health component; the still-active personality and scenes remain usable.

Set `CONTENT_RELOAD_ENABLED=0` to remove the live operation while retaining normal startup loading. Reload changes content only: it never reads or writes visitor transcripts, conversation memory, MQTT credentials, or saved operator settings.

## Health and performance

The runtime performs explicit startup checks for MQTT, saved settings, the personality library, the Vosk microphone path, Piper, Ollama, the PIR, and PCA9685 animation hardware. It reports one of five overall states:

- `healthy`: all enabled paths are ready.
- `degraded`: the skeleton remains usable, but an optional path or preferred fast path is unavailable. Examples include no PIR with MQTT trigger still working, Ollama using the local spoken fallback, or legacy Piper replacing streaming audio.
- `unhealthy`: a critical function is unsafe or unavailable, such as no usable Piper path, critical CPU temperature, or critically full storage. **Ready** turns off in this state.
- `starting` / `stopping`: startup checks or shutdown are in progress.

The health sensor exposes each component's state and detail through Home Assistant's [MQTT JSON attributes](https://www.home-assistant.io/integrations/sensor.mqtt/#json-attributes-topic-configuration), while `sensor.skeleton_health_reasons` gives the short actionable summary. Every 30 seconds, a daemon monitor reads CPU temperature, one-minute load, memory, disk, uptime, and Raspberry Pi throttle flags. It also makes a lightweight Ollama `/api/tags` request; it never generates text for the periodic check. Monitoring runs outside the controller and audio callbacks, and any monitor failure is isolated from conversation operation.

Home Assistant also receives rolling averages and P95 values over the latest 20 samples for TTS first audio, greeting first audio, response first audio, and full Ollama reply time. `sensor.skeleton_audio_dropped_frames` should remain at zero; a rising value means the microphone callback is producing data faster than recognition consumes it.

Configuration:

- `HEALTH_INTERVAL_SEC`: system/probe interval; default `30` seconds.
- `HEALTH_LATENCY_WINDOW`: number of recent turns in each rolling statistic; default `20`.
- `HEALTH_TEMP_WARN_C` / `HEALTH_TEMP_CRITICAL_C`: default `75` / `82` °C.
- `HEALTH_DISK_WARN_PERCENT` / `HEALTH_DISK_CRITICAL_PERCENT`: default `90` / `97` percent used.
- `OLLAMA_HEALTHCHECK_ENABLED`: set to `0` to disable only the periodic `/api/tags` probe.

The monitor uses standard Linux files and the optional Raspberry Pi [`vcgencmd get_throttled`](https://www.raspberrypi.com/documentation/computers/os.html#get_throttled) command, so it adds no Python dependency. Historical throttle flags remain visible even after the immediate condition clears; only current throttle bits degrade health.

## Operator self-test

The **Run Self-Test** Home Assistant button verifies the installed output path without waiting for a visitor. It is manual-only and accepted only while the controller is idle or in cooldown. The serialized controller performs, in order:

1. two eye pulses capped at 35% brightness;
2. two jaw movements capped at 35% of configured travel;
3. one cached line through the interruptible streaming Piper path.

This confirms that commands reach the PCA9685 eye channel, jaw servo, and live speaker path. It cannot visually or acoustically judge the physical result, so the operator still observes the skeleton while the test runs. A missing output is marked `skipped` and produces a `degraded` result; a hardware write error produces `failed`. The structured per-step report is attached to `sensor.skeleton_self_test_last_result`.

PIR activity, any other subscribed MQTT command, **Stop Self-Test**, shutdown, or a spoken barge-in command during the speaker step interrupts the sequence. The jaw always returns to its configured rest position and the eyes return to idle. Legacy Piper is reported as a skipped speaker step because its temporary WAV playback cannot be stopped within the existing 20 ms safety boundary. The self-test never starts automatically during service or Pi startup.

Configuration:

- `SELF_TEST_ENABLED`: enables the manual controls; default `1`.
- `SELF_TEST_MAX_SECONDS`: hard runtime bound from `1` to `60` seconds; default `12`.
- `SELF_TEST_EYES_FRAC`: requested eye level, clamped to `5–35%` and further capped by night mode.
- `SELF_TEST_JAW_FRAC`: requested portion of configured jaw travel, clamped to `5–35%`.
- `SELF_TEST_STEP_SEC`: hold time for each pulse/movement, clamped to `0.10–1.0` seconds.
- `SELF_TEST_LINE`: short speaker verification phrase, included in the canned-speech cache.

## systemd hang recovery

The packaged service uses `Type=notify` and does not become ready until Piper, the canned cache, Ollama, scenes, personalities, and saved settings have completed their startup work. The runtime sends native `READY=1`, `STATUS=...`, `STOPPING=1`, and `WATCHDOG=1` datagrams directly to systemd; no extra Python package is required.

`WatchdogSec=60` is enabled in the base service. A dedicated daemon checks a heartbeat produced by the serialized controller, not the independent health-monitor thread. It feeds systemd only while that controller heartbeat is no more than `WATCHDOG_CONTROLLER_STALE_SEC` old (45 seconds by default). If the controller deadlocks, watchdog feeds stop and the existing `Restart=always` policy replaces the process. Normal bounded listening, Ollama, scene, and speech operations refresh the controller heartbeat; the configured stale limit is clamped below systemd's deadline to prevent an unsafe configuration from defeating recovery.

Home Assistant exposes whether systemd enabled the watchdog, its state, controller-heartbeat age, last successful feed, feed count, and last error. Running the Python script manually has no `NOTIFY_SOCKET` or `WATCHDOG_USEC`, so watchdog state is neutrally `disabled` and all skeleton features continue to work.

## Checks

Run the hardware-free checks from `skeleton_project/`:

```bash
python3 -m unittest discover -s tests -v
python3 -m compileall -q .
```

## License
[MIT](LICENSE)
