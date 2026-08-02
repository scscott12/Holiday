# v1.0 Raspberry Pi acceptance and release

This is the release gate for the first production Holiday Skeleton. GitHub CI proves the hardware-free behavior; this procedure proves the installed Raspberry Pi, wiring, mechanics, audio, recovery paths, and sustained operation. Do not create the `v1.0` tag until the evidence command reports `passed` and its SHA-256 digest verifies.

The acceptance tool only reads systemd, verifies the non-venv runtime files against the active release manifest, and captures bounded journal counters, state-file modes, Pi temperature/throttle state, memory, disk, and uptime. It never reads MQTT credentials, prompts, conversations, transcripts, or audio. Operator notes are limited to 240 characters and reject common credential forms. The evidence file and digest are written atomically with mode `0600`.

## Safety boundary

Maintenance Mode is an operating interlock, not an electrical disconnect. Keep clear of moving parts during ordinary tests. Before the watchdog or deployment-failure exercises, lock Maintenance Mode, disconnect servo/LED actuator power, and verify the jaw is at rest and the eyes are dark. Do not reconnect actuator power until the service has recovered healthy and the lock is still active.

The release and acceptance tools do not update Raspberry Pi OS, firmware, MQTT credentials, Home Assistant, Ollama models, Piper voices, or operator content. The acceptance tool never moves hardware or injects a fault itself.

## 1. Prepare one exact candidate

Run the CI gate on the exact commit intended for release:

```bash
cd /path/to/Holiday/skeleton_project
python3 -m pip install -r requirements-ci.txt
python3 -m unittest discover -s tests -v
python3 -m compileall -q holiday_skeleton scripts tests skeleton_all_in_one_mqtt.py
python3 -c "import skeleton_all_in_one_mqtt"
python3 scripts/validate_project.py
SKELETON_RC_COMMIT=$(git rev-parse HEAD)
git status --short
```

The status output must be empty and `SKELETON_RC_COMMIT` must be the merged, CI-passing release-candidate commit.

Rehearse both rollback paths from that source before installing the final candidate identity:

```bash
sudo python3 scripts/deploy_release.py --release-id v1.0-rc1-rollback-rehearsal

# Lock Maintenance Mode, disconnect actuator power, and verify safe outputs first.
sudo python3 scripts/deploy_release.py \
  --release-id v1.0-rc1-injected-failure \
  --simulate-activation-failure \
  --confirm-maintenance-lockout
```

The injected command must exit with failure after reporting that automatic rollback succeeded. Confirm `current` still points to `v1.0-rc1-rollback-rehearsal`, the prior settings/content/unit were restored, and the service is healthy. Then exercise the last-successful-deployment rollback and install the final candidate:

```bash
sudo python3 scripts/deploy_release.py --rollback
sudo python3 scripts/deploy_release.py --release-id v1.0-rc1
sudo systemctl enable holiday-skeleton
sudo systemctl status holiday-skeleton --no-pager
```

Confirm `/opt/holiday-skeleton/current` resolves to `releases/v1.0-rc1` and its `release-manifest.json` contains the exact full commit in `SKELETON_RC_COMMIT`. Existing settings, journal, personalities, scenes, sounds, and the systemd override must remain unchanged.

Initialize the private evidence bundle only after the final candidate is active and healthy:

```bash
SKELETON_RC_EVIDENCE=/var/lib/holiday-skeleton-deploy/acceptance/v1.0-rc1.json
sudo python3 scripts/release_candidate.py init \
  --candidate v1.0-rc1 \
  --expected-commit "$SKELETON_RC_COMMIT" \
  --evidence "$SKELETON_RC_EVIDENCE"
```

Set `SKELETON_RC_EVIDENCE` to that same absolute path again after any reboot or new shell before running later record/sample/finalize commands.

## 2. Record the acceptance checks

Use Home Assistant plus `journalctl -u holiday-skeleton` to perform each observation. Record `failed` immediately when the result is not acceptable. Correct the issue, repeat the complete check, and add a later `passed` attempt; the bundle retains both attempts.

The record command is:

```bash
sudo python3 scripts/release_candidate.py record \
  --evidence "$SKELETON_RC_EVIDENCE" \
  --check CHECK_ID \
  --result passed \
  --note "Short factual observation with no visitor text or credentials"
```

Complete these checks:

1. `deploy_verified` — service is `active/running`, native readiness completed, the watchdog is `1min`, the active manifest matches `v1.0-rc1` and the expected commit, and operator content/state survived deployment.
2. `calibration_self_test` — in Maintenance Mode with Night Mode off, walk all nine calibration steps; save and restart; then run the manual self-test and physically observe both eyes, both bounded jaw moves, audible speech, jaw rest, and idle eyes. Repeat cancellation and interruption paths.
3. `conversation_audio` — run two motion visits and Home Assistant Say; confirm streaming Piper, canned greeting cache hits, no temporary-WAV path, jaw/audio alignment, follow-up memory within one visit, memory clearing afterward, first audio before full Ollama completion, and zero dropped audio frames.
4. `barge_in_preemption` — during long speech verify `wait` returns to listening and `stop` ends without goodbye. During idle speech/self-test/scene, verify PIR and MQTT interruption restore jaw/eyes before higher-priority work begins. Ordinary generated speech containing command words must not interrupt itself.
5. `scenes_content` — run and stop each packaged scene. Reload one harmless valid content change, reject malformed JSON/missing WAV without replacing the working libraries, and interrupt a reload before commit. Restore the original packaged/operator content when finished.
6. `maintenance_lockout` — lock during active speech/scene, confirm immediate silence, eyes off, jaw rest, blocked queued/new actuator commands, and no PIR visit. Restart and confirm the lock persists. Unlock only after moving clear.
7. `settings_restart` — save personality, motion/idle, night, eye/volume, maintenance, and calibrated hardware values; restart cleanly and confirm exact restoration. Both state files must be regular `0600` files.
8. `power_cycle` — shut down cleanly, remove Pi/prop power for at least 30 seconds, restore power, and confirm automatic boot, network/MQTT reconnect, systemd readiness, saved settings, content, maintenance state, watchdog, Home Assistant discovery, and one normal visitor flow. No `unclean_restart` should be recorded for the clean shutdown.
9. `watchdog_recovery` — lock Maintenance Mode, disconnect actuator power, and confirm safe outputs. Record the main PID and then run `sudo systemctl kill --kill-whom=main --signal=STOP holiday-skeleton`. Within the watchdog/restart window, systemd must replace the process, readiness and MQTT telemetry must return, `NRestarts` must increment once, and the journal must record the unclean recovery. If it does not recover within two minutes, run `sudo systemctl kill --kill-whom=main --signal=CONT holiday-skeleton` followed by `sudo systemctl restart holiday-skeleton`, mark the check failed, and diagnose before continuing.
10. `deployment_rollback` — record the earlier double-confirmed `v1.0-rc1-injected-failure` result: the staged link switched, the candidate never started, and the prior link/settings/content/unit/service were restored by the real automatic rollback transaction.
11. `manual_rollback` — record the earlier `--rollback` result: it restored the exact prior release and snapshot, refused stale/repeated rollback, and the final `v1.0-rc1` redeployment returned healthy.
12. `journal_privacy` — confirm bounded retention, monotonic sequences, expected restart/reload/self-test/calibration/maintenance events, and capped Home Assistant recent attributes. Inspect locally for visitor phrases, prompts, replies, broker usernames/passwords, bearer/access/refresh tokens, client secrets, and API keys; none may be present. Do not copy any visitor text into the acceptance note.

Check progress at any time:

```bash
sudo python3 scripts/release_candidate.py status --evidence "$SKELETON_RC_EVIDENCE"
```

## 3. Overnight soak

Begin the soak only after checks 1–12 pass, all injected faults are finished, the final candidate is healthy, actuator power is safely restored, and no work is queued:

```bash
sudo python3 scripts/release_candidate.py begin-soak --evidence "$SKELETON_RC_EVIDENCE"
```

Leave normal motion, idle life, MQTT, Home Assistant, Piper, Ollama, PIR, watchdog, and the installed content enabled for at least eight continuous hours. Do not deploy, reboot, restart the service, edit content/settings, or intentionally disconnect dependencies during this period. The gate automatically rejects a changed release/commit, unhealthy service, missing watchdog, current undervoltage/throttling, critical temperature, critical disk/memory use, unsafe state-file modes, or a changed systemd restart count.

Capture at least one intermediate sample near the middle of the run:

```bash
sudo python3 scripts/release_candidate.py sample --evidence "$SKELETON_RC_EVIDENCE"
```

After eight hours, inspect the jaw linkage, servo, LED driver, wiring, connectors, power supplies, amplifier, speaker, Pi, and PCA9685. Nothing may be loose, binding, unusually hot, discolored, noisy, or reset-prone. Confirm final Home Assistant health, latency, dropped-frame, journal, and watchdog values, then record `final_inspection` as passed with the factual observation.

```bash
sudo python3 scripts/release_candidate.py record \
  --evidence "$SKELETON_RC_EVIDENCE" \
  --check final_inspection \
  --result passed \
  --note "Post-soak linkage, wiring, power, audio, and health inspection passed"
```

Finalize; this takes the required third healthy sample, verifies the full record, and writes a SHA-256 sidecar:

```bash
sudo python3 scripts/release_candidate.py finalize \
  --evidence "$SKELETON_RC_EVIDENCE" \
  --operator "Sean Scott"
sudo python3 scripts/release_candidate.py verify --evidence "$SKELETON_RC_EVIDENCE"
```

If finalization says more time or samples are needed, leave the same candidate running, take another sample later, and retry. Never edit the JSON or digest manually.

## 4. Create `v1.0`

Only after verification succeeds, create an annotated tag on the exact accepted commit—not on a later branch tip—and open the GitHub release:

```bash
git tag -a v1.0 "$SKELETON_RC_COMMIT" -m "Holiday Skeleton v1.0"
git push origin v1.0
```

Release notes should identify the accepted commit, candidate release ID, acceptance date/operator, evidence SHA-256, CI result, Pi model/OS, and any intentionally degraded optional component. Keep the private evidence bundle under `/var/lib/holiday-skeleton-deploy/acceptance`; do not publish it or the diagnostic journal unless deliberately reviewed and redacted.
