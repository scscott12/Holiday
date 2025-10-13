# Holiday Skeleton (PCA9685 + MQTT + Home Assistant)

Animatronic skeleton powered by Raspberry Pi:
- Eyes via PCA9685 PWM (OFF idle, DIM listening/thinking, BRIGHT speaking)
- Jaw via PCA9685 Servo, driven by TTS envelope
- PIR-gated conversation (Vosk STT + Piper TTS + optional Ollama LLM)
- Clean Home Assistant MQTT Discovery controls
- Pretty Markdown transcript

See `INSTALL.txt` for step-by-step setup.

What’s inside:
- skeleton_all_in_one_mqtt.py — the single-file service (eyes/jaw/PIR/Vosk/Piper/Ollama + MQTT + HA discovery).
- requirements.txt — Python deps.
- systemd/holiday-skeleton.service and systemd/override.conf.example — drop-in unit + env overrides (put quotes around passwords with !).
- scripts/ — MQTT utilities:
  - nuke_discovery.sh (clears old HA discovery for this device)
  - publish_minimal_discovery.py (publishes the clean, minimal controls)
  - list_discovery.sh (view current HA discovery topics)
- ha/helpers.yaml + ha/automation_say_from_input_text.yaml — optional HA helper + automation to type a line and make him speak.
- prompts.json.example — override system prompt + opening/closing lines.
- README.md — short project blurb.
- INSTALL.txt — step-by-step setup from scratch (RasPi packages → venv → systemd → HA integration).

Quick start (super short):
- unzip, copy skeleton_all_in_one_mqtt.py + requirements.txt to /opt/holiday-skeleton, create venv, pip install -r requirements.txt.
- install the systemd unit + override.conf (edit broker creds; keep quotes if your password has !).
- systemctl enable --now holiday-skeleton, then journalctl -u holiday-skeleton -f.
- (Optional) run scripts/nuke_discovery.sh skeleton then scripts/publish_minimal_discovery.py.
- In Home Assistant → MQTT → discover the device.
