# Observability (OBSERVABILITY.md)

The project is a single-process Pi service with **no HTTP server**, so there are no web requests, no
request IDs, no distributed traces and no request metrics. Observability is MQTT state topics, journald
logs and a bounded event journal. Files are under `skeleton_project/`.

## Logs

- The service prints tagged lines (`[mqtt]`, `[config]`, `[controller]`, `[event journal]`, ...) to stdout, which
  systemd sends to journald (`sudo journalctl -u holiday-skeleton -f`; `PYTHONUNBUFFERED=1`).
- Payload contents are not logged: `_on_message` logs topic and byte size only.
- `holiday_skeleton/event_journal.py`: bounded JSON history at `EVENT_JOURNAL_PATH`
  (default `/var/lib/holiday-skeleton/diagnostic-events.json`, 16-512 entries). Fields are category,
  code, severity, source, detail; detail passes `sanitize_detail`. Last events also go to MQTT
  `journal/*` retained topics. Prior unclean shutdown is detected and logged.

## Request IDs and traces

Not present. MQTT commands carry no correlation ID, and log lines carry none. No tracing library.
A session's events can only be correlated by time.

## Metrics measured

Published as retained MQTT topics under `holiday/<DEVICE_NAME>/`, mainly for Home Assistant
(`holiday_skeleton/health.py`, `discovery.py`):

- `health/status`, `health/ok`, `health/reasons`, `health/components` (per-component state).
- `health/latency/<name>_average|_p95|_samples` for `tts_first_audio`, `greeting_first_audio`,
  `response_first_audio`, `llm_reply` (window `HEALTH_LATENCY_WINDOW`, default 20).
- Pi telemetry (temperature, disk and memory, throttling) with warn/critical thresholds
  `HEALTH_TEMP_*`, `HEALTH_DISK_*`, published every `HEALTH_INTERVAL_SEC`.
- `watchdog/*` (controller age, feed count, last error), `journal/*` counts, `availability` (LWT),
  `ready`, `status`, plus maintenance, calibration, content reload and self-test state.
- Labels: there are no label dimensions; topics are keyed by device name only. No user IDs or emails
  exist or are used. Visitor text must never be placed in topics or the journal.

## Alerts

- **systemd watchdog**: `WatchdogSec=60`, `Restart=always`, `RestartSec=3` restart a hung service
  (`holiday_skeleton/watchdog.py`; stale threshold `WATCHDOG_CONTROLLER_STALE_SEC`, 45s).
- **MQTT LWT** publishes `availability=offline` when the service disappears.
- Health state changes are written to the journal (`_event_journal_record`).
- **Not present**: no external pager/email/push alert rules. The owner must build Home Assistant
  automations on `availability`, `health/ok` and `health/status` (examples under `ha/`).

## Reliability targets (SLOs)

None are defined in the repository. Proposed starting point: service `availability=online` 99.5%
per week, and `response_first_audio` p95 under a figure set after measuring on the Pi.

## Gaps found

1. No request or correlation IDs in logs; no tracing.
2. No error-rate or request-count metrics (only latency and component health).
3. No alerts leave the house by default; Home Assistant automations are not shipped.
4. No SLOs documented or measured.
5. Logs are plain text, not structured; journald retention is the host default.
6. Latency window is the last 20 samples, so brief spikes are hard to see over time; there is no
   long-term metrics store.
