# Threat model (THREAT_MODEL.md)

## Assets

- **MQTT credentials** (`MQTT_USER`, `MQTT_PASS`, optional TLS client key): control of the prop and a
  foothold on the home network.
- **The physical prop**: servos, speaker and microphone; unsafe or abusive output (speech, motion).
- **Visitor privacy**: spoken words captured by the microphone and sent to the local Ollama model.
  Not persisted (`brain.py` memory resets each visit), but processed in memory.
- **Private content**: `prompts.json` and any acceptance evidence.
- **The Pi itself** (root via deployment, `scripts/deploy_release.py`).
- No money, no accounts, no personal records are stored.

## Attackers

- A device on the same network sniffing or spoofing the MQTT broker connection.
- Anyone with broker credentials or access to Home Assistant (insider or compromised account).
- A visitor shouting commands or injecting prompts at the speech input.
- Someone with physical access to the Pi or SD card.
- A malicious or corrupted release package.

## STRIDE

| Threat | Stopped today | Not stopped yet |
| --- | --- | --- |
| **Spoofing** | Broker username/password (`_configure_mqtt_security`); optional TLS and client cert; LWT availability. | TLS off by default; no sender identity; a rogue broker is not detected without a CA (AUTH.md). |
| **Tampering** | Payload size cap and type parsing (`_on_message`); schema-validated JSON (`settings.py`, `scene.py`, `personality.py`); atomic writes; sha256 release manifest (`deployment.py`); `resolve_sound_path` blocks path traversal; maintenance lockout. | Unauthenticated tampering by any broker-authorised client; no release signature. |
| **Repudiation** | `EventJournal` records bounded operational events (rejections, maintenance, health); service logs to journald. | Events have no sender identity; journal is bounded (16-512) so old events roll off. |
| **Information disclosure** | `sanitize_detail` redaction; visitor text not persisted or logged by the journal; credentials only from environment; `.gitignore` for `.env`, `prompts.json`, `*.wav`. | Plaintext MQTT by default; retained error topics not sanitised; any subscriber reads health/journal topics. |
| **Denial of service** | Bounded controller queue with coalescing; `MQTT_MAX_PAYLOAD_BYTES`; motion cooldown; maintenance lockout; systemd `Restart=always` and `WatchdogSec`; health/watchdog monitor; scene length cap (`SCENE_MAX_SECONDS`). | No per-sender rate limit; loud audio can trigger barge-in and speech; Ollama and Piper subprocess load. |
| **Elevation of privilege** | Non-root service user, `NoNewPrivileges`, `Protect*`, `RestrictSUIDSGID`; no shell via `subprocess` with user text (fixed argv lists to Piper and player). | `restart/set` and `content/reload/set` open to any publisher; deployment runs as root; prompt injection through speech could steer the LLM (output is only spoken). |

## Gaps found

See AUTH.md, SECURITY.md and RLS_POLICIES.md. Highest priority: enable TLS by default,
enforce per-role broker ACLs, add per-sender rate limiting at the broker, and sanitise retained
error topics.
