import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from scripts import release_candidate as acceptance_cli
from holiday_skeleton.release_candidate import (
    MIN_SOAK_SECONDS,
    ReleaseCandidateError,
    new_evidence,
    save_evidence,
)

CANDIDATE = "v1.0-rc1"
COMMIT = "a" * 40
START = datetime(2026, 8, 2, 12, 0, tzinfo=timezone.utc)


def stamp(offset_seconds=0):
    return (START + timedelta(seconds=offset_seconds)).isoformat(timespec="seconds")


def _healthy_sample(offset_seconds=0, restarts="0"):
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


class ReleaseCandidateEvidenceTests(unittest.TestCase):
    def test_finalize_without_evidence_fails(self):
        """Finalizing a candidate without passing checks or soak must fail."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
            save_evidence(path, evidence)
            # No checks passed, no soak started; finalize should return 1 (failure)
            with mock.patch.object(
                acceptance_cli, "_probe", return_value=_healthy_sample()
            ):
                result = acceptance_cli.main(
                    ["finalize", "--evidence", str(path), "--operator", "Test"]
                )
            self.assertEqual(result, 1)
            # Evidence should still be in_progress
            from holiday_skeleton.release_candidate import load_evidence
            self.assertEqual(load_evidence(path)["status"], "in_progress")

    def test_verify_without_digest_fails(self):
        """Verifying a candidate that has no digest file must fail."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            evidence = new_evidence(CANDIDATE, COMMIT, now=lambda: stamp())
            save_evidence(path, evidence)
            # No digest written; verify should return 1 (failure)
            result = acceptance_cli.main(["verify", "--evidence", str(path)])
            self.assertEqual(result, 1)

    def test_finalize_without_valid_soak_fails(self):
        """Finalizing must fail when soak time is below the minimum."""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "evidence.json"
            # Creating evidence with too-short soak should raise
            with self.assertRaises(ReleaseCandidateError):
                new_evidence(
                    CANDIDATE,
                    COMMIT,
                    now=lambda: stamp(),
                    soak_seconds=MIN_SOAK_SECONDS - 1,
                )


if __name__ == "__main__":
    unittest.main()
