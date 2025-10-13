# Holiday Skeleton (PCA9685 + MQTT + Home Assistant)

Animatronic skeleton powered by Raspberry Pi:
- Eyes via PCA9685 PWM (OFF idle, DIM listening/thinking, BRIGHT speaking)
- Jaw via PCA9685 Servo, driven by TTS envelope
- PIR-gated conversation (Vosk STT + Piper TTS + optional Ollama LLM)
- Clean Home Assistant MQTT Discovery controls
- Pretty Markdown transcript

See `INSTALL.txt` for step-by-step setup.
