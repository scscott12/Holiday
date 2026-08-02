import json
import os
import subprocess
import tempfile
import unittest
from collections import namedtuple
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import deploy_release as deploy_cli
from scripts import release_candidate as acceptance_cli

from holiday_skeleton.release_candidate import (
    ACCEPTANCE_CHECKS,
    MIN_SOAK_SECONDS,
    ReleaseCandidateError,
    SystemProbe,
    add_soak_sample,
    begin_soak,
    finalize_evidence,
    load_evidence,
    new_evidence,
    record_check,
    save_evidence,
    validate_sample,
    verify_digest,
    write_digest,
)


CANDIDATE = "v1.0-rc1"
COMMIT = "a" * 40
START = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def stamp(offset_seconds=0):
    return (START + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def healthy_sample(offset_seconds=0, restarts="0"):
    return {
        "captured_at": stamp(offset_seconds),
        "release": {
            "id": CANDIDATE,
            "source_commit": COMMIT,
            "path": f"/opt/holiday-skeleton/releases/{CANDIDATE}",
            "manifest_sha256": "b" * 64,
            "runtime_files_verified": 24,
        },
        "service": {
            "ActiveState": "active",
            "SubState": "running",
            "Result": "success",
            "ExecMainStatus": "0",
            "MainPID": "123",
            "NRestarts": restarts,
            "WatchdogUSec": "1min",
        },
        "pi": {
            "temperature_c": 51.2,
            "throttle_hex": "0x0",
            "current_throttle_flags": 0,
            "memory_used_percent": 42.5,
            "disk_used_percent": 36.0,
            "uptime_seconds": 1000.0 + offset_seconds,
        },
        "state": {
            "settings_mode": "0600",
            "journal": {
                "mode": "0600",
                "events": 12,
                "warnings": 1,
                "errors": 0,
                "latest_code": "health_recovered",
                "active_session": True,
            },
        },
    }


def pass_checks(payload, include_final=True):
    for check in ACCEPTANCE_CHECKS:
        if include_final or check.identifier != "final_inspection":
            record_check(
                payload,
                check.identifier,
                "passed",
                f"Observed {check.title.lower()} and retained local evidence",
                now=lambda: stamp(60),
            )


class ReleaseCandidateEvidenceTests(unittest.TestCase):
    def test_release_guide_names_every_acceptance_check(self):
        guide = Path(__file__).resolve().parents[1].joinpath("RELEASE.md").read_text(
            encoding="utf-8"
        )
        for check in ACCEPTANCE_CHECKS:
            with self.subTest(check=check.identifier):
                self.assertIn(f"`{check.identifier}`", guide)

    def test_candidate_name_commit_and_soak_bounds_are_strict(self):
        evidence = new_evidence(
            CANDIDATE, COMMIT, now=lambda: stamp(), soak_seconds=MIN_SOAK_SECONDS
        )
        self.assertEqual(evidence["candidate"], CANDIDATE)
        self.assertEqual(len(evidence["checks"]), len(ACCEPTANCE_CHECKS))
        for candidate in ("v1.0", "v1.0-rc0", "v2.0-rc1", "../v1.0-rc1"):
            with self.subTest(candidate=candidate):
                with self.assertRaises(ReleaseCandidateError):
                    new_evidence(candidate, COMMIT)
        with self.assertRaisesRegex(ReleaseCandidateError, "40-character"):
            new_evidence(CANDIDATE, "abc")
        with self.assertRaisesRegex(ReleaseCandidateError, "between 8 and 72"):
            new_evidence(CANDIDATE, COMMIT, soak_seconds=MIN_SOAK_SECONDS - 1)

    def test_check_attempts_are_auditable_and_can_recover_from_failure(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        record_check(
            evidence,
            "deploy_verified",
            "failed",
            "First activation did not become ready",
            now=lambda: stamp(10),
        )
        record_check(
            evidence,
            "deploy_verified",
            "passed",
            "Corrected output device and deployment passed",
            now=lambda: stamp(20),
        )
        record = evidence["checks"]["deploy_verified"]
        self.assertEqual(record["status"], "passed")
        self.assertEqual([attempt["result"] for attempt in record["attempts"]], ["failed", "passed"])

    def test_notes_reject_credentials(self):
        evidence = new_evidence(CANDIDATE, COMMIT)
        with self.assertRaisesRegex(ReleaseCandidateError, "credentials"):
            record_check(
                evidence,
                "deploy_verified",
                "passed",
                "mqtt_pass=do-not-store-this",
            )

    def test_soak_cannot_start_before_prerequisites_pass(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        with self.assertRaisesRegex(ReleaseCandidateError, "prerequisites"):
            begin_soak(evidence, healthy_sample(), now=lambda: stamp(100))

    def test_final_inspection_is_allowed_after_soak_starts(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        pass_checks(evidence, include_final=False)
        begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
        self.assertEqual(evidence["soak"]["status"], "running")
        self.assertEqual(evidence["checks"]["final_inspection"]["status"], "pending")

    def test_unhealthy_sample_is_rejected(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        pass_checks(evidence, include_final=False)
        bad = healthy_sample(100)
        bad["pi"]["current_throttle_flags"] = 1
        with self.assertRaisesRegex(ReleaseCandidateError, "throttle"):
            begin_soak(evidence, bad, now=lambda: stamp(100))

    def test_full_eight_hour_three_sample_gate_finalizes(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        pass_checks(evidence, include_final=False)
        begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
        add_soak_sample(evidence, healthy_sample(4 * 3600))
        record_check(
            evidence,
            "final_inspection",
            "passed",
            "Linkage remained clear and wiring stayed cool and secure",
            now=lambda: stamp(MIN_SOAK_SECONDS),
        )
        finalize_evidence(
            evidence,
            healthy_sample(MIN_SOAK_SECONDS + 101),
            "Sean Scott",
            now=lambda: stamp(MIN_SOAK_SECONDS + 101),
        )
        self.assertEqual(evidence["status"], "passed")
        self.assertEqual(evidence["soak"]["status"], "passed")
        self.assertEqual(evidence["signoff"]["operator"], "Sean Scott")

    def test_finalize_rejects_short_soak_and_restart_change(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        pass_checks(evidence)
        begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
        add_soak_sample(evidence, healthy_sample(200))
        with self.assertRaisesRegex(ReleaseCandidateError, "more seconds"):
            finalize_evidence(
                evidence,
                healthy_sample(300),
                "Operator",
                now=lambda: stamp(300),
            )
        self.assertEqual(len(evidence["soak"]["samples"]), 2)

        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        pass_checks(evidence)
        begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
        add_soak_sample(evidence, healthy_sample(4 * 3600, restarts="1"))
        with self.assertRaisesRegex(ReleaseCandidateError, "restart count changed"):
            finalize_evidence(
                evidence,
                healthy_sample(MIN_SOAK_SECONDS + 101, restarts="1"),
                "Operator",
                now=lambda: stamp(MIN_SOAK_SECONDS + 101),
            )

    def test_finalize_rejects_three_last_minute_samples_without_mid_run_evidence(self):
        evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
        pass_checks(evidence)
        begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
        add_soak_sample(evidence, healthy_sample(MIN_SOAK_SECONDS - 1))
        with self.assertRaisesRegex(ReleaseCandidateError, "mid-run sample"):
            finalize_evidence(
                evidence,
                healthy_sample(MIN_SOAK_SECONDS + 101),
                "Operator",
                now=lambda: stamp(MIN_SOAK_SECONDS + 101),
            )

    def test_finalized_evidence_is_atomic_private_and_digest_detects_tamper(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.0-rc1.json"
            evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
            pass_checks(evidence)
            begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
            add_soak_sample(evidence, healthy_sample(4 * 3600))
            finalize_evidence(
                evidence,
                healthy_sample(MIN_SOAK_SECONDS + 101),
                "Operator",
                now=lambda: stamp(MIN_SOAK_SECONDS + 101),
            )
            save_evidence(path, evidence)
            digest_path = write_digest(path)

            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(os.stat(digest_path).st_mode & 0o777, 0o600)
            self.assertEqual(len(verify_digest(path)), 64)
            loaded = load_evidence(path)
            self.assertEqual(loaded["status"], "passed")

            path.write_text(path.read_text() + " ", encoding="utf-8")
            with self.assertRaisesRegex(ReleaseCandidateError, "does not match"):
                verify_digest(path)

    def test_digest_can_be_recreated_for_unchanged_final_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.0-rc1.json"
            evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
            pass_checks(evidence)
            begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
            add_soak_sample(evidence, healthy_sample(4 * 3600))
            finalize_evidence(
                evidence,
                healthy_sample(MIN_SOAK_SECONDS + 101),
                "Operator",
                now=lambda: stamp(MIN_SOAK_SECONDS + 101),
            )
            save_evidence(path, evidence)
            first = write_digest(path)
            first.unlink()
            second = write_digest(path)
            self.assertEqual(second, first)
            self.assertEqual(len(verify_digest(path)), 64)

    def test_finalized_bundle_is_revalidated_not_just_hashed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "v1.0-rc1.json"
            evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
            pass_checks(evidence)
            begin_soak(evidence, healthy_sample(100), now=lambda: stamp(100))
            add_soak_sample(evidence, healthy_sample(4 * 3600))
            finalize_evidence(
                evidence,
                healthy_sample(MIN_SOAK_SECONDS + 101),
                "Operator",
                now=lambda: stamp(MIN_SOAK_SECONDS + 101),
            )
            evidence["checks"]["deploy_verified"]["status"] = "failed"
            with self.assertRaisesRegex(ReleaseCandidateError, "latest attempt"):
                save_evidence(path, evidence)

    def test_disabled_watchdog_is_not_a_healthy_sample(self):
        sample = healthy_sample()
        sample["service"]["WatchdogUSec"] = "0"
        self.assertTrue(
            any(
                "watchdog" in failure
                for failure in validate_sample(sample, CANDIDATE, COMMIT)
            )
        )

    def test_evidence_symlink_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real = root / "real.json"
            save_evidence(real, new_evidence(CANDIDATE, COMMIT))
            link = root / "link.json"
            os.symlink(real, link)
            with self.assertRaisesRegex(ReleaseCandidateError, "regular file"):
                load_evidence(link)

    def test_sample_reports_each_release_and_health_failure(self):
        sample = healthy_sample()
        self.assertEqual(validate_sample(sample, CANDIDATE, COMMIT), [])
        sample["release"]["source_commit"] = "b" * 40
        sample["service"]["ActiveState"] = "failed"
        failures = validate_sample(sample, CANDIDATE, COMMIT)
        self.assertTrue(any("commit" in failure for failure in failures))
        self.assertTrue(any("not active" in failure for failure in failures))


class SystemProbeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.prefix = self.root / "opt" / "holiday-skeleton"
        self.release = self.prefix / "releases" / CANDIDATE
        self.release.mkdir(parents=True)
        os.symlink(Path("releases") / CANDIDATE, self.prefix / "current")
        main = self.release / "skeleton_all_in_one_mqtt.py"
        requirements = self.release / "requirements.txt"
        main.write_text("VALUE = 1\n", encoding="utf-8")
        requirements.write_text("paho-mqtt\n", encoding="utf-8")
        (self.release / "release-manifest.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "release_id": CANDIDATE,
                    "source_commit": COMMIT,
                    "created_at": stamp(),
                    "files": {
                        "skeleton_all_in_one_mqtt.py": {
                            "type": "file",
                            "bytes": main.stat().st_size,
                            "sha256": file_hash(main),
                        },
                        "requirements.txt": {
                            "type": "file",
                            "bytes": requirements.stat().st_size,
                            "sha256": file_hash(requirements),
                        },
                        "venv/bin/python": {
                            "type": "symlink",
                            "target": "/usr/bin/python3",
                        },
                    },
                }
            ),
            encoding="utf-8",
        )
        self.state = self.root / "state"
        self.state.mkdir()
        settings = self.state / "operator-settings.json"
        settings.write_text('{"version": 3}\n', encoding="utf-8")
        settings.chmod(0o600)
        journal = self.state / "diagnostic-events.json"
        journal.write_text(
            json.dumps(
                {
                    "version": 1,
                    "active_session": "abc123abc123",
                    "next_sequence": 3,
                    "maximum_entries": 128,
                    "events": [
                        {"severity": "info", "code": "runtime_started"},
                        {"severity": "warning", "code": "health_degraded"},
                    ],
                }
            ),
            encoding="utf-8",
        )
        journal.chmod(0o600)
        self.temperature = self.root / "temperature"
        self.temperature.write_text("51250\n", encoding="utf-8")
        self.meminfo = self.root / "meminfo"
        self.meminfo.write_text(
            "MemTotal: 1000000 kB\nMemAvailable: 600000 kB\n", encoding="utf-8"
        )
        self.uptime = self.root / "uptime"
        self.uptime.write_text("12345.6 1000.0\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def runner(command, **_kwargs):
        if command[0] == "systemctl":
            output = (
                "ActiveState=active\nSubState=running\nResult=success\n"
                "ExecMainStatus=0\nMainPID=321\nNRestarts=0\n"
                "WatchdogUSec=1min\n"
            )
        elif command[0] == "vcgencmd":
            output = "throttled=0x50000\n"
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, stdout=output, stderr="")

    def test_probe_collects_bounded_credential_free_pi_evidence(self):
        probe = SystemProbe(
            runner=self.runner,
            now=lambda: stamp(),
            temperature_path=self.temperature,
            meminfo_path=self.meminfo,
            uptime_path=self.uptime,
        )
        with mock.patch(
            "holiday_skeleton.release_candidate.shutil.disk_usage",
            return_value=shutil_usage(total=1000, used=400, free=600),
        ):
            sample = probe.capture(prefix=self.prefix, state_directory=self.state)

        self.assertEqual(sample["release"]["id"], CANDIDATE)
        self.assertEqual(sample["release"]["runtime_files_verified"], 2)
        self.assertEqual(sample["pi"]["temperature_c"], 51.25)
        self.assertEqual(sample["pi"]["current_throttle_flags"], 0)
        self.assertEqual(sample["state"]["settings_mode"], "0600")
        self.assertEqual(sample["state"]["journal"]["warnings"], 1)
        rendered = json.dumps(sample).lower()
        self.assertNotIn("password", rendered)
        self.assertNotIn("token", rendered)

    def test_probe_rejects_active_release_outside_managed_tree(self):
        (self.prefix / "current").unlink()
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(outside, self.prefix / "current")
        probe = SystemProbe(
            runner=self.runner,
            temperature_path=self.temperature,
            meminfo_path=self.meminfo,
            uptime_path=self.uptime,
        )
        with self.assertRaisesRegex(ReleaseCandidateError, "outside managed"):
            probe.capture(prefix=self.prefix, state_directory=self.state)


class ReleaseCandidateCliTests(unittest.TestCase):
    def test_cli_initializes_records_and_reports_without_real_hardware(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = Path(directory) / "candidate.json"
            with mock.patch.object(
                acceptance_cli, "_probe", return_value=healthy_sample()
            ):
                self.assertEqual(
                    acceptance_cli.main(
                        [
                            "init",
                            "--candidate",
                            CANDIDATE,
                            "--expected-commit",
                            COMMIT,
                            "--evidence",
                            str(evidence),
                        ]
                    ),
                    0,
                )
            self.assertEqual(
                acceptance_cli.main(
                    [
                        "record",
                        "--evidence",
                        str(evidence),
                        "--check",
                        "deploy_verified",
                        "--result",
                        "passed",
                        "--note",
                        "Active manifest and service verified",
                    ]
                ),
                0,
            )
            self.assertEqual(
                acceptance_cli.main(["status", "--evidence", str(evidence)]), 0
            )
            self.assertEqual(
                load_evidence(evidence)["checks"]["deploy_verified"]["status"],
                "passed",
            )

    def test_fault_injection_requires_exact_double_confirmation(self):
        self.assertEqual(
            deploy_cli.main(
                ["--simulate-activation-failure", "--release-id", "test-fault"]
            ),
            2,
        )
        self.assertEqual(
            deploy_cli.main(["--confirm-maintenance-lockout"]),
            2,
        )


def shutil_usage(total, used, free):
    return namedtuple("usage", "total used free")(total, used, free)


def file_hash(path):
    import hashlib

    return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
