# Holiday roadmap

Owner-editable draft for issue #48, based on checkout `b294f76`, the [README](skeleton_project/README.md), runtime code, and issue/PR history reviewed on 2026-10-28. “Current” means present in this checkout, not proven on the installed prop.

The direction is a responsive, dependable Raspberry Pi skeleton operated through Home Assistant. Use this draft to favor changes that improve visitor interaction, safe hardware control, and recoverable operation while preserving one serialized controller, bounded actions, and local speech/fallback behavior.

## Current State

- PIR-triggered conversations combine offline Vosk recognition, warm Piper speech with jaw movement, PWM eyes, and optional streamed Ollama replies with bounded, visit-scoped memory. MQTT discovery exposes Home Assistant controls and diagnostics.
- Interruptible idle behaviors and scenes, three personality packs, transactional content reload, persistent settings, maintenance lockout, guided calibration, and manual self-test are implemented. `brain.py`, `barge_in.py`, `personality.py`, `scene.py`, and `watchdog.py` contain working implementations rather than feature placeholders.
- Deployment stages versioned releases, checks readiness, and supports rollback. Health reporting, a bounded diagnostic journal, systemd watchdog recovery, and private release-acceptance tooling exist.
- Recent PRs improved USB audio compatibility (#28), speaker-echo rejection and delayed command corroboration (#29–30), and quieter visits with varied greetings and interaction-only transcripts (#36). Hardware-free CI runs unit tests, compile/import checks, and packaged-content validation; #31–34 added test documentation and regression coverage.

## Half-Built / In Progress

- **Production acceptance:** [RELEASE.md](skeleton_project/RELEASE.md) documents rejected `v1.0-rc1` through `rc3` and a corrected `rc4` procedure. The release tooling is built; successful physical acceptance is a separate milestone whose status needs owner confirmation.
- **Transcript notifications:** [PR #36](https://github.com/scscott12/Holiday/pull/36) supplies full transcripts through the Home Assistant `full_text` attribute. The Telegram automation lives outside this repository; that PR could not verify the complete notification path and was not tested on the Pi.
- **Compatibility limits:** legacy Piper playback lacks the streaming path's interruption support; speech scenes require streaming Piper, and legacy sound cues ignore per-cue volume. These are documented limits, not confirmed promises of future parity.

## Planned Next

1. Follow the documented first-production-release gate: test the exact candidate, rehearse recovery and rollback, verify physical behavior, and complete an uninterrupted eight-hour soak with three healthy samples. Create `v1.0` only after the private evidence and digest verify ([release procedure](skeleton_project/RELEASE.md), PR #20).
2. Continue the explicit quality backlog: add measured coverage reporting and a minimum CI threshold ([#35](https://github.com/scscott12/Holiday/issues/35)), and conservative static linting ([#47](https://github.com/scscott12/Holiday/issues/47)). Both issues were open when reviewed; neither gate is present in this checkout.
3. Carry recent audio and quiet-visit fixes through physical acceptance, checking real visitor speech, false triggers, interruption, and notification delivery. PR #36's hardware validation remains unconfirmed; broader feature priorities belong in the owner questions below.

## Risks

- **Physical safety and reliability:** servo travel, power, wiring, heat, and outdoor acoustics require real hardware checks. Maintenance mode is a software interlock; hands-on wiring or linkage work requires disconnected actuator power ([wiring notes](skeleton_project/WIRING.md)).
- **Deployment regressions:** earlier candidates failed through watchdog reporting, rollback ownership/permissions, and slow dependency installation. Unit tests alone cannot establish reliable Pi upgrades or recovery.
- **Conversation quality:** speaker echo, ambient noise, device rates, and Ollama latency can cause missed commands, false visits, or slow responses despite local fallbacks.
- **Privacy and integration:** settings and diagnostic evidence exclude conversations, but transcripts are published over MQTT. External Home Assistant/Telegram consumers determine further storage and delivery behavior.

## Questions for the owner

- Has `rc4` or a later candidate passed physical acceptance and the soak? Which commit and private evidence record should define the production baseline?
- Has the external Telegram automation been updated to read `full_text`, and has complete visitor speech delivery been verified?
- What Pi/audio setup and acceptable response latency, false-trigger rate, and interruption behavior should future decisions target?
- After release hardening, should priority go to more characters/scenes, wider hardware support, or additional holiday props? Is legacy Piper feature parity worth pursuing?
- What transcript retention and notification policy should operators follow outside this repository?
