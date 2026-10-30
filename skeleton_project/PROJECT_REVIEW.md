# Skeleton project review

This review covers the runtime, packaged content, Home Assistant discovery,
deployment path, service unit, and hardware-free tests as of this branch.

## Corrected in this change

- **Empty visits sounded like goodbyes.** A PIR trigger always played a farewell
  when speech recognition timed out, even when nobody had interacted. Empty
  visits now end after the opening line. A farewell is used only after a real
  visitor exchange or an explicit exit phrase.
- **Character variety had contracted.** Each personality had six openings per
  daypart and one shared pool of six farewells. Each personality now has ten
  openings and ten farewells for morning, afternoon, evening, and night. The
  loader still accepts the older shared-farewell format.
- **Home Assistant mixed operation and diagnostics.** The example dashboard is
  split into Operate, Scenes, Setup & Test, and Diagnostics views. MQTT discovery
  marks configuration and diagnostic entities so the generated device page is
  easier to scan.
- **MQTT transport had no TLS option.** TLS with CA verification and optional
  client certificates is now supported. Oversized command payloads are rejected
  and recorded instead of entering the controller queue.
- **The service had only basic process isolation.** The unit now blocks privilege
  escalation, private temporary files, kernel/module/control-group changes, and
  setuid/setgid creation while retaining required GPIO, I2C, audio, and video
  access.

## Security assessment

### Good controls already present

- MQTT and PIR callbacks enqueue work; one controller serializes foreground
  actions.
- Maintenance mode blocks motion, speech, scenes, effects, and self-test
  commands, with diagnostic records for rejected actions.
- Personality, scene, calibration, and settings inputs have bounded schemas.
- Settings, event history, release evidence, and deployment state use restrictive
  file modes and atomic replacement.
- Deployment uses a process lock, versioned staging, readiness checks, snapshots,
  and automatic rollback. Subprocesses use argument arrays rather than a shell.
- Event-journal details are sanitized and visitor transcripts are published only
  after interaction.

### Operator controls still required

1. **Use broker ACLs.** The runtime intentionally trusts authenticated commands
   on its MQTT command topics. Limit the skeleton account to its own topic tree;
   do not give untrusted LAN clients publish access.
2. **Use TLS outside a trusted isolated LAN.** Set `MQTT_TLS=1` and validate the
   broker with a CA certificate. Do not expose port 1883 to the internet.
3. **Keep credentials in the root-managed systemd override.** Never commit the
   live override or copy credentials into `prompts.json` or personality files.
4. **Keep physical power separation.** Maintenance mode is a software lockout.
   Actuator power must remain independently disconnectable for wiring work.
5. **Pin and review release dependencies.** Runtime packages other than Piper are
   currently range-unpinned. A future release process should build and test a
   locked ARM64 dependency set before field deployment.

## Structure assessment

The package modules have clear boundaries for speech, scenes, personalities,
settings, health, watchdog, deployment, and acceptance. The remaining structural
risk is `skeleton_all_in_one_mqtt.py`: it still combines configuration, MQTT,
hardware setup, visit orchestration, content reload, calibration, and lifecycle
management in one large integration module.

Refactor it in tested slices rather than rewriting the runtime:

1. Extract typed environment configuration and validation.
2. Extract MQTT command routing and state publication.
3. Extract visit orchestration (greeting, listening, reply, farewell).
4. Leave hardware construction and process lifecycle in the entry point.

Each extraction should preserve the serialized controller, maintenance
interrupts, watchdog heartbeat, and hardware-free import behavior.

## Functional acceptance still needed on the prop

- Confirm all four dayparts select from their own opening and farewell pools.
- Confirm a motion trigger with no speech plays one opening and no farewell.
- Confirm a real conversation ends with one daypart-appropriate farewell.
- Refresh MQTT discovery or restart Home Assistant so entity categories and the
  reorganized dashboard are visible.
- Re-run the physical calibration, scene, watchdog, power-cycle, and soak checks
  required by `RELEASE.md` before assigning a final release tag.
