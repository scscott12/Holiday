# Row-level access policy (RLS_POLICIES.md)

**Not applicable as row-level security.** The project has no database, no tables, no user accounts
and no roles. There are no per-user rows to isolate, so there are no RLS policies. This was checked
by reading `skeleton_all_in_one_mqtt.py` and `holiday_skeleton/`; there is no SQL driver, ORM, or
connection string. It is an animatronic prop, so visitors do not own records.

## Stores of records that do exist

| Store | Who may read | Add | Change | Delete | Enforced by |
| --- | --- | --- | --- | --- | --- |
| `operator-settings.json` (`PERSIST_SETTINGS_PATH`, `holiday_skeleton/settings.py`) | The service user; operators indirectly via retained MQTT state | Service, from MQTT controls | Service, from MQTT controls/calibration (maintenance mode only for calibration) | Operator with OS access | File ownership of the systemd `StateDirectory` (mode 0750), atomic write and schema validation in `settings.py` |
| `diagnostic-events.json` (`EVENT_JOURNAL_PATH`, `event_journal.py`) | Service user; last N events published to `journal/recent` | Service only (bounded 16-512 entries) | Not editable; old entries rolled off | Operator with OS access | Same directory permissions; `sanitize_detail` |
| `personalities.json`, `scenes.json`, `prompts.json` (private) | Anyone with the package on disk | Deployer | Deployer; hot reload via `content/reload/set` | Deployer | Deployment user, validation (`personality.py`, `scene.py`, `content.py`) |
| Deployment state and backups (`deployment.py`) | root | root | root | root | Modes 0700/0750, `os.chmod` in `deployment.py` |
| Visit memory (`brain.py`) | In-process only | Service | Service | Reset at end of visit | Never persisted |

## Roles

There are no application roles (owner/editor/read-only). The effective roles are:

- **MQTT operator**: anyone the broker lets publish to `holiday/<DEVICE_NAME>/.../set`. All have the same
  powers. There is no read-only operator in the service.
- **Maintenance lockout** (`maintenance/set`, `_MAINTENANCE_BLOCKED_EVENTS`): when on, motion triggers,
  speech, scenes and similar actuation are rejected; calibration and content reload are separate.
- **Host administrator**: OS user with `sudo` for deployment.

## Gaps found

1. No distinction between read-only and control access inside the service. Use broker ACLs: read-only
   subscribers on `holiday/<DEVICE_NAME>/#` (no publish), a separate operator account for `/set` topics.
2. The runtime user in `systemd/holiday-skeleton.service` is hard-coded (`User=scscott`).
3. Settings and journal file modes are set by systemd `StateDirectoryMode=0750`; the code in
   `settings.py` does not set its own file mode (`deployment.py` does for its files).
4. Retained MQTT topics (health, journal, errors) are readable by every broker subscriber.
