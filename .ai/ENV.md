# Environment variables and settings (ENV.md)

All values are read in `skeleton_project/skeleton_all_in_one_mqtt.py` through `envs()` (which wraps
`os.getenv`), except where noted. There is no browser UI, so **nothing is public/browser-safe**: every
variable is private to the Pi unless marked "non-secret". Values live in the systemd override
(`/etc/systemd/system/holiday-skeleton.service.d/override.conf`, template
`systemd/override.conf.example`), not in the repo. Defaults below are those in code.

## Secrets (private, never commit)

| Variable | Purpose | Where the value lives |
| --- | --- | --- |
| `MQTT_PASS` | MQTT broker password | systemd override (placeholder in `override.conf.example`); also read by `scripts/publish_minimal_discovery.py` |
| `MQTT_USER` | MQTT username (treat as private) | systemd override |
| `MQTT_KEYFILE` | Path to TLS client private key | File on disk, path in override |
| `MQTT_CERTFILE` | Path to TLS client certificate | File on disk, path in override |

## Connection and identity (private)

`MQTT_HOST`, `MQTT_PORT` (1883), `MQTT_TLS` (0), `MQTT_CA_CERT`, `MQTT_MAX_PAYLOAD_BYTES` (4096, bounded 64-65536),
`DEVICE_NAME` (`skeleton`; forms the topic prefix `holiday/<name>`), `OLLAMA_CHAT_URL` / `OLLAMA_URL`
(`http://127.0.0.1:11434/api/chat`), `OLLAMA_MODEL`, `KEEP_ALIVE`.

## Paths and models (non-secret)

`HOME`, `MODEL_PATH` (Vosk), `PIPER_BIN`, `PIPER_MODEL`, `PIPER_CONFIG`, `TTS_WAV` (`/tmp/tts.wav`), `PROMPTS_PATH`
(private file `prompts.json`; gitignored), `SCENES_PATH`, `SCENE_SOUND_DIR`, `PERSONALITIES_PATH`,
`PERSIST_SETTINGS_PATH`, `EVENT_JOURNAL_PATH`.

## Behaviour and tuning (non-secret)

- Hardware: `PCA_FREQ`, `JAW_CH`, `EYES_CH`, `JAW_MIN_US`, `JAW_MAX_US`, `JAW_REST_FRAC`, `JAW_MAX_FRAC`,
  `EYES_IDLE_FRAC`, `EYES_LISTEN_FRAC`, `EYES_SPEAK_FRAC`, `EYES_INVERT`, `PIR_PIN`, `MOTION_HOLD_SEC`,
  `MOTION_COOLDOWN_SEC`.
- Audio: `AUDIO_INPUT_DEVICE`, `AUDIO_OUTPUT_DEVICE`, `AUDIO_OUTPUT_SAMPLE_RATE`, `TTS_FRAME_MS`,
  `TTS_CANNED_CACHE`, `VOLUME`, `VOSK_RATE`, `SD_BLOCKSIZE`, `ENERGY_GATE`, `MIN_TEXT_LEN`, `PREROLL_SEC`,
  `NO_SPEECH_TIMEOUT`, `SPEECH_START_TIMEOUT`, `MIN_VOICED_SEC`, `END_SILENCE_SEC`, `MAX_UTTERANCE_SEC`.
- Barge-in: `BARGE_IN_ENABLED`, `BARGE_IN_STOP_COMMANDS`, `BARGE_IN_LISTEN_COMMANDS`, `BARGE_IN_WAKE_WORDS`,
  `BARGE_IN_ENERGY_GATE`, `BARGE_IN_MIN_VOICED_SEC`, `BARGE_IN_PARTIAL_CONFIRMATIONS`,
  `BARGE_IN_CAPTURE_RATE`, `BARGE_IN_REQUIRE_WAKE_WORD`.
- Idle life: `IDLE_LIFE_ENABLED`, `IDLE_LIFE_MIN_SEC`, `IDLE_LIFE_MAX_SEC`, `IDLE_MUTTER_CHANCE`,
  `IDLE_EYE_PULSE_FRAC`, `IDLE_EYE_PULSE_MS`, `IDLE_JAW_TWITCH_FRAC`, `IDLE_JAW_TWITCH_MS`.
- Content: `SCENES_ENABLED`, `SCENE_MAX_SECONDS`, `PERSONALITIES_ENABLED`, `PERSONALITY`, `CONTENT_RELOAD_ENABLED`,
  `PERSIST_SETTINGS_ENABLED`, `MAINTENANCE_MODE`.
- Journal: `EVENT_JOURNAL_ENABLED`, `EVENT_JOURNAL_MAX_ENTRIES` (128, 16-512), `EVENT_JOURNAL_RECENT_ENTRIES` (20, 1-50).
- Health and watchdog: `HEALTH_INTERVAL_SEC`, `HEALTH_LATENCY_WINDOW`, `HEALTH_TEMP_WARN_C`, `HEALTH_TEMP_CRITICAL_C`,
  `HEALTH_DISK_WARN_PERCENT`, `HEALTH_DISK_CRITICAL_PERCENT`, `OLLAMA_HEALTHCHECK_ENABLED`,
  `WATCHDOG_CONTROLLER_STALE_SEC`.
- Self-test and calibration: `SELF_TEST_ENABLED`, `SELF_TEST_MAX_SECONDS`, `SELF_TEST_EYES_FRAC`,
  `SELF_TEST_JAW_FRAC`, `SELF_TEST_STEP_SEC`, `SELF_TEST_LINE`, `CALIBRATION_ENABLED`, `CALIBRATION_PREVIEW_SEC`,
  `CALIBRATION_MIC_SAMPLE_SEC`, `CALIBRATION_LINE`.
- LLM: `LLM_MEMORY_TURNS`, `LLM_CONTEXT_TOKENS`, `LLM_MAXIMUM_TOKENS`, `LLM_TEMPERATURE`, `LLM_REPEAT_PENALTY`,
  `LLM_PHRASE_MIN_CHARS`, `LLM_PHRASE_SOFT_CHARS`, `LLM_PHRASE_MAX_CHARS`.

## Set by systemd (read, not configured by the operator)

`NOTIFY_SOCKET`, `WATCHDOG_USEC`, `WATCHDOG_PID` (read in `holiday_skeleton/watchdog.py`), `LG_WD`,
`PYTHONUNBUFFERED` (set in `systemd/holiday-skeleton.service`).

## Other scripts

`scripts/publish_minimal_discovery.py` reads `MQTT_HOST`, `MQTT_PORT`, `MQTT_USER`, `MQTT_PASS`, `DEVICE_NAME`
and `PERSONALITIES_PATH` from the shell environment.

## `.env` and examples

- `skeleton_project/.gitignore` contains `.env` and `prompts.json`; `git ls-files` shows no `.env` committed.
- The committed examples are `systemd/override.conf.example` (placeholders `<ipAddress>`, `<Username>`,
  `your!pass`) and `prompts.json.example`. There is no `.env.example`, because the service reads the
  systemd environment, not a dotenv file.
- The repository root has no `.gitignore`; only `skeleton_project/.gitignore` applies.

## Gaps found

1. No `.env.example` (the issue asks for one). Either add one or keep this note that the override
   example takes its place.
2. The code defaults `MQTT_HOST` to the literal `<ipAddress>` and `MQTT_USER` to `<Username>`, so a
   missing variable fails late at connect time rather than at startup.
3. Many numeric variables use bare `int()`/`float()` (a bad value crashes startup with a traceback).
   Only a few use `bounded_env_int`.
4. The root `.gitignore` is missing, so `.env` in the repository root would not be ignored.
5. `override.conf.example` contains the text `your!pass`; keep it a placeholder and do not reuse it.
