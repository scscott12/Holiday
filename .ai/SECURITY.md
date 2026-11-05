# Security rules (SECURITY.md)

Every change must follow these rules. Files are under `skeleton_project/` unless noted.
This is a Pi service driven over MQTT; there is no web UI, no database, and no user login.
Where a rule does not apply as written, the section says how it maps to this project.

## Rules

1. **No secrets in code.** Credentials come from the environment (`envs()` in
   `skeleton_all_in_one_mqtt.py`) via the systemd override. Never commit real MQTT credentials,
   `.env`, private `prompts.json`, visitor text, audio or acceptance evidence
   (`.gitignore` excludes `.env`, `prompts.json`, `*.wav`, venvs). Use placeholders in examples.
2. **Validate every input on the server.** All outside input arrives as MQTT payloads
   (`_on_message`), JSON content files, or environment values. Check type, range and size before use;
   raise specific errors (`SceneConfigError`, `SettingsConfigError`, `CalibrationConfigError`,
   `EventJournalError`). There is no SQL, so "parameterised queries" is N/A (see SQL injection).
3. **Authenticate and authorise every request.** There is no per-request user. Authentication is the
   broker's (username/password, optional TLS client cert); authorisation is the broker's topic ACLs plus
   the service's maintenance lockout (`_maintenance_stop_requested`, `_maintenance_reject`). New topics
   must be added to the subscription list in `_on_connect`, the maintenance table
   (`_MAINTENANCE_BLOCKED_EVENTS` / `_maintenance_topic_action`) when they actuate hardware, and to the
   broker ACL guidance.
4. **Rate limiting.** There is no login route and no per-user identity, so the "global limit before
   login / per-user limit after login" split cannot be implemented as written. What exists instead:
   a bounded controller queue (`queue.Queue(maxsize=...)` in `holiday_skeleton/controller.py`) that
   coalesces or drops excess events (`_enqueue` logs "coalesced or dropped"), a motion cooldown
   (`MOTION_COOLDOWN_SEC`), and an MQTT payload cap (`MQTT_MAX_PAYLOAD_BYTES`, 64-65536, default 4096).
   See Gaps found.
5. **Least privilege.** `systemd/holiday-skeleton.service` runs as a non-root user with only the
   `gpio i2c audio video` groups, `NoNewPrivileges`, `PrivateTmp`, `ProtectKernel*`, `RestrictSUIDSGID`,
   and a private `StateDirectory` (mode 0750). The MQTT account should be limited to the
   `holiday/<DEVICE_NAME>/#` topics at the broker.

## The seven holes

**SQL injection.** Not applicable: no database or SQL anywhere (`rg -i sql` finds none). State is
JSON files written atomically (`holiday_skeleton/settings.py`, `event_journal.py`).
If a database is ever added, use parameterised queries only.

**IDOR / BOLA.** Not applicable: there are no per-user records or ids. Visitors have no stored
records (memory is in-process, per visit, `LLM_MEMORY_TURNS`). The nearest equivalent is names sent
over MQTT (scene, personality). These are looked up in an in-memory library of validated names
(`holiday_skeleton/scene.py`, `personality.py`) and unknown names are rejected, not opened as paths.
Sound files are resolved by `resolve_sound_path` (`scene.py`), which rejects absolute paths and `..` and
checks the resolved path stays under `SCENE_SOUND_DIR`.

**Mass assignment.** Payloads are never bound directly onto objects. Each MQTT topic maps to one
fixed action with one parsed value (`_on_message`). JSON content and settings are parsed field by
field with exact-field checks (`event_journal._exact_fields`, `settings.py`, `calibration.py`,
`scene._validate_step`), size-capped (`MAX_SETTINGS_BYTES`), and unknown fields are rejected.

**Insecure deserialisation.** Only `json` is used (`json.load`/`json.loads`). No `pickle`, `yaml.load`,
`eval`, or `exec` in runtime code (`rg 'pickle|eval\(|exec\('` over `holiday_skeleton/` and the entry
point). PyYAML is a CI-only dependency (`requirements-ci.txt`). Persisted files are validated on load
(`settings.py`, `event_journal.py`).

**Sensitive data exposure.** `holiday_skeleton/event_journal.py` `sanitize_detail` redacts URL
credentials, bearer tokens and `secret=value` forms and bounds length; the journal never stores
conversations, prompts or credentials. Visitor speech is not persisted. `_on_message` logs the topic and
size but not the payload. Gaps: MQTT retained `*/last_error` topics publish raw `str(error)[:255]`
(not passed through `sanitize_detail`), and TLS is off by default (AUTH.md).

**Broken authentication.** Delegated to the MQTT broker; see AUTH.md. In the service: credentials are
only from the environment, and `will_set` marks the device offline on loss. Gaps are listed in AUTH.md.

**Cross-site scripting (XSS).** Not applicable: the service renders no HTML. Home Assistant renders
the MQTT values (`ha/*.yaml`, `holiday_skeleton/discovery.py`). Texts that come from MQTT `say/set`
are only spoken. Retained state topics include operator-supplied text (for example scene and personality
names, error strings); any dashboard showing them must escape them.

## Gaps found

1. No rate limiting by sender; a client with broker access can flood `say/set` or `scene/play/set`.
   The bounded queue protects memory, not fairness. Enforce rate limits at the broker.
2. `say/set` text has no maximum length of its own beyond `MQTT_MAX_PAYLOAD_BYTES` (4096 default).
3. `restart/set`, `content/reload/set` and `maintenance/set` have no confirmation or second factor.
   `maintenance/set` itself is allowed during lockout (by design) so a sender can turn the lockout off.
4. Error strings published on retained topics are not passed through `sanitize_detail`.
5. No dependency vulnerability scanning in `.github/workflows/skeleton-ci.yml`; `requirements.txt`
   pinning should be checked.
6. Bare `except:` in `_on_message` (eyes/volume handlers) silently swallows bad values.
7. Release integrity: `deployment.py` records and checks `sha256` of files, but there is no signature
   check of the release source.
