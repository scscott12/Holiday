# Project guide

## What this project is for

Holiday Skeleton is a single-process Python animatronic prop for a Raspberry Pi.
Visitors trigger it with motion and talk to it; operators control it through MQTT
and Home Assistant. It uses Vosk speech recognition, Piper speech synthesis,
optional Ollama replies, a PCA9685-controlled jaw and eyes, personality packs,
and scripted scenes. Start with [the README](skeleton_project/README.md).

## How it is laid out

- `skeleton_project/skeleton_all_in_one_mqtt.py`: service entry point, environment
  settings, and hardware/network integration.
- `skeleton_project/holiday_skeleton/`: controller, audio and conversation logic,
  scenes, personalities, calibration, settings, health, watchdog, deployment,
  and release acceptance modules.
- `skeleton_project/tests/`: hardware-free `unittest` tests with mocks and
  temporary files.
- `skeleton_project/scripts/`: content validation, deployment and release
  acceptance commands, plus MQTT discovery utilities.
- `skeleton_project/ha/`: Home Assistant YAML examples.
- `skeleton_project/systemd/`: service unit and example environment override.
- `skeleton_project/personalities.json`, `scenes.json`, and
  `prompts.json.example`: packaged character/scene content and example prompts.
- `.github/workflows/skeleton-ci.yml`: Python 3.11 unit, compile, import, and
  packaged-content checks. `INSTALL.txt`, `WIRING.md`, and `RELEASE.md` under
  `skeleton_project/` describe physical installation and acceptance.

## How to run it and how to test it

Run development checks from `skeleton_project/`. The README's dependency setup
is `python3 -m pip install -r requirements-ci.txt` (NumPy and PyYAML); it was not
run while writing this guide. Runtime dependencies are in `requirements.txt`.

These exact commands were attempted from `skeleton_project/`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 python3 -c "import skeleton_all_in_one_mqtt"
PYTHONDONTWRITEBYTECODE=1 python3 scripts/validate_project.py
PYTHONPYCACHEPREFIX=/tmp/holiday-49-pycache python3 -m compileall -q holiday_skeleton scripts tests skeleton_all_in_one_mqtt.py
```

Compilation passed. The suite reported 169 tests with 17 errors from missing
`numpy`; import and content validation also failed for that missing dependency.
The bytecode settings keep generated caches outside the checkout. The issue's
required `true` command passed from the repository root; it checks no behavior.

For actual operation, follow [INSTALL.txt](skeleton_project/INSTALL.txt) and
[WIRING.md](skeleton_project/WIRING.md). They require Pi system packages,
hardware access groups, a configured systemd override, and the connected prop.
Configure MQTT, audio devices, and external Vosk/Piper model paths for that Pi.
The documented install/start sequence, from `skeleton_project/`, is:

```bash
sudo python3 scripts/deploy_release.py
sudo systemctl enable holiday-skeleton
sudo journalctl -u holiday-skeleton -f
```

These production commands were checked against the documentation, not executed.
The deployer activates the service; `enable` arranges startup at boot. The unit
runs the entry point through `/opt/holiday-skeleton/current/venv/bin/python`.
Running the entry point directly initializes hardware; it is not a dry run.

## Rules and conventions

- Follow nearby Python style: four-space indentation, `snake_case` functions
  and variables, `PascalCase` classes, uppercase constants/environment names,
  and private helpers prefixed with `_`. Package modules use type hints,
  docstrings, dataclasses, and enums; the integration script has denser legacy
  formatting. Avoid unrelated reformatting.
- MQTT and PIR callbacks enqueue work. The serialized controller owns speech
  and actuation. Preserve cancellation, maintenance lockout, bounded durations,
  and cleanup that returns outputs to safe states. Keep imports hardware-free.
- Validate input with specific exceptions such as `SceneConfigError` and
  `SettingsConfigError`. Content reload prepares changes before committing;
  invalid or interrupted reloads retain working content. Runtime integration
  reports failures through status/health and diagnostic output; controller
  exceptions set the error state and propagate to cleanup.
- Add hardware-free `test_*.py` tests for behavior changes. Recent history adds
  schema and validator tests and fixes false motion visits and speaker echo;
  preserve those regression cases and visitor-text privacy boundaries.

## Leave alone

- Never commit real MQTT credentials, `.env`, private `prompts.json`, visitor
  text, audio, or private acceptance evidence. `.gitignore` also excludes WAVs,
  virtual environments, and Python bytecode; do not force-add generated files.
- Do not casually run discovery utilities: `nuke_discovery.sh` removes retained
  discovery, and the publisher changes broker state.
- Treat `/opt/holiday-skeleton` releases, `current`, shared content/sounds,
  `/var/lib/holiday-skeleton` state, deployment backups, and systemd overrides
  as operator-managed data. Use the deployer's verified switch/rollback path;
  rollback restores earlier settings and content too. Preserve ownership and
  permissions. Do not hand-edit manifests or acceptance digests.
- Maintenance Mode is not an electrical disconnect. Calibration previews can
  energize outputs while locked. Disconnect servo/LED power before wiring or
  linkage work; keep jaw limits conservative and use the specified LED driver.
- Follow [RELEASE.md](skeleton_project/RELEASE.md) before release tagging: it
  requires physical acceptance, rollback checks, and an eight-hour soak with
  verified private evidence. Unit tests cannot establish physical safety.

## Open questions for the owner

- Which Pi, audio devices, model files, and calibration values are authoritative
  for the current installation? The repository supplies defaults and examples.
- Should the service's hard-coded `scscott` user/group be retained for other
  installations, and what account should they use?
- Has the documented `v1.0-rc4` candidate completed physical acceptance and the
  soak? The private evidence and current deployed state are not in this checkout.
