import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from holiday_skeleton.deployment import (
    DeploymentError,
    DeploymentPaths,
    ReleaseDeployer,
    SystemdManager,
    validate_release_id,
)


class FakeSystemd(SystemdManager):
    def __init__(self, start_failures=None):
        super().__init__("holiday-skeleton")
        self.events = []
        self.start_failures = list(start_failures or [])

    @staticmethod
    def command_available(name):
        return True

    def stop(self, timeout=30.0):
        self.events.append("stop")

    def daemon_reload(self):
        self.events.append("daemon-reload")

    def start_and_verify(self, timeout, settle_seconds):
        self.events.append("start-and-verify")
        if self.start_failures and self.start_failures.pop(0):
            raise DeploymentError("simulated readiness failure")


class TestReleaseDeployer(ReleaseDeployer):
    def _prepare_runtime(self, release):
        python = release / "venv" / "bin" / "python"
        python.parent.mkdir(parents=True)
        os.symlink("/usr/bin/python3", python)


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.prefix = self.root / "opt" / "holiday-skeleton"
        self.state = self.root / "state" / "holiday-skeleton"
        self.unit = self.root / "etc" / "holiday-skeleton.service"
        self._make_source()

    def tearDown(self):
        self.temporary.cleanup()

    def _make_source(self):
        (self.source / "holiday_skeleton").mkdir(parents=True)
        (self.source / "systemd").mkdir()
        (self.source / "holiday_skeleton" / "__init__.py").write_text(
            '"""test package"""\n', encoding="utf-8"
        )
        (self.source / "skeleton_all_in_one_mqtt.py").write_text(
            "VALUE = 1\n", encoding="utf-8"
        )
        (self.source / "requirements.txt").write_text("", encoding="utf-8")
        (self.source / "personalities.json").write_text(
            '{"personalities": []}\n', encoding="utf-8"
        )
        (self.source / "scenes.json").write_text(
            '{"scenes": []}\n', encoding="utf-8"
        )
        (self.source / "systemd" / "holiday-skeleton.service").write_text(
            "[Service]\n"
            "WorkingDirectory=/opt/holiday-skeleton/current\n"
            "ExecStart=/opt/holiday-skeleton/current/venv/bin/python "
            "/opt/holiday-skeleton/current/skeleton_all_in_one_mqtt.py\n",
            encoding="utf-8",
        )

    def _deployer(self, systemd=None):
        return TestReleaseDeployer(
            DeploymentPaths(
                source=self.source,
                prefix=self.prefix,
                state_directory=self.state,
                service_unit=self.unit,
            ),
            systemd=systemd or FakeSystemd(),
            minimum_free_bytes=0,
            settle_seconds=0,
            now=lambda: "2026-08-02T12:00:00+00:00",
        )

    def _legacy_install(self):
        self.prefix.mkdir(parents=True, exist_ok=True)
        self.state.mkdir(parents=True, exist_ok=True)
        self.unit.parent.mkdir(parents=True, exist_ok=True)
        self.unit.write_text("legacy unit\n", encoding="utf-8")
        (self.prefix / "personalities.json").write_text(
            '{"personalities": ["custom"]}\n', encoding="utf-8"
        )
        (self.prefix / "scenes.json").write_text(
            '{"scenes": ["custom"]}\n', encoding="utf-8"
        )
        (self.prefix / "sounds").mkdir()
        (self.prefix / "sounds" / "custom.wav").write_bytes(b"RIFF-custom")
        (self.state / "operator-settings.json").write_text(
            '{"version": 3}\n', encoding="utf-8"
        )
        (self.state / "diagnostic-events.json").write_text(
            '{"version": 1}\n', encoding="utf-8"
        )

    def _old_versioned_install(self):
        self._legacy_install()
        old = self.prefix / "releases" / "old"
        old.mkdir(parents=True)
        (old / "old.txt").write_text("working", encoding="utf-8")
        os.symlink(Path("releases") / "old", self.prefix / "current")
        return old

    def test_release_id_is_strict_and_path_safe(self):
        self.assertEqual(validate_release_id("v1.0-abc_12"), "v1.0-abc_12")
        for value in ("", ".", "..", "../escape", "bad name", "staging-x"):
            with self.subTest(value=value):
                with self.assertRaises(DeploymentError):
                    validate_release_id(value)

    def test_successful_deploy_preserves_shared_content_and_records_exact_release(self):
        self._legacy_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        result = deployer.deploy("v19-test")

        self.assertEqual(result.release_id, "v19-test")
        self.assertIsNone(result.previous_release)
        self.assertEqual(self.prefix.joinpath("current").resolve(), result.release_path)
        self.assertEqual(
            (self.prefix / "personalities.json").read_text(encoding="utf-8"),
            '{"personalities": ["custom"]}\n',
        )
        self.assertEqual(
            (self.prefix / "sounds" / "custom.wav").read_bytes(), b"RIFF-custom"
        )
        self.assertIn(
            str(self.prefix / "current"),
            self.unit.read_text(encoding="utf-8"),
        )
        manifest = json.loads(
            (result.release_path / "release-manifest.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["release_id"], "v19-test")
        self.assertEqual(manifest["files"]["venv/bin/python"]["type"], "symlink")
        self.assertEqual(systemd.events, ["stop", "daemon-reload", "start-and-verify"])
        record = json.loads(deployer.paths.record.read_text())
        self.assertEqual(record["status"], "active")
        self.assertEqual(record["backup_path"], str(result.backup_path))
        self.assertEqual(os.stat(result.backup_path).st_mode & 0o777, 0o700)
        self.assertEqual(
            result.backup_path.joinpath("state/operator-settings.json").read_text(),
            '{"version": 3}\n',
        )

    def test_failed_health_check_restores_versioned_release_unit_state_and_content(self):
        old = self._old_versioned_install()
        original_settings = (self.state / "operator-settings.json").read_bytes()
        original_content = (self.prefix / "scenes.json").read_bytes()
        systemd = FakeSystemd(start_failures=[True, False])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("bad-release")

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(self.unit.read_text(encoding="utf-8"), "legacy unit\n")
        self.assertEqual((self.state / "operator-settings.json").read_bytes(), original_settings)
        self.assertEqual((self.prefix / "scenes.json").read_bytes(), original_content)
        self.assertEqual(
            systemd.events,
            [
                "stop",
                "daemon-reload",
                "start-and-verify",
                "stop",
                "daemon-reload",
                "start-and-verify",
            ],
        )

    def test_failure_after_backup_but_before_link_switch_still_restores_transaction(self):
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        def fail_seed(_release):
            (self.state / "operator-settings.json").write_text("changed", encoding="utf-8")
            raise DeploymentError("simulated content activation error")

        deployer._seed_shared_content = fail_seed

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("seed-failure")

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(encoding="utf-8"),
            '{"version": 3}\n',
        )

    def test_deliberate_post_switch_failure_exercises_real_automatic_rollback(self):
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(
            DeploymentError, "operator-requested activation failure"
        ):
            deployer.deploy("acceptance-fault", simulate_activation_failure=True)

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(self.unit.read_text(encoding="utf-8"), "legacy unit\n")
        self.assertEqual(
            systemd.events,
            ["stop", "daemon-reload", "stop", "daemon-reload", "start-and-verify"],
        )

    def test_failed_fresh_install_removes_seeded_content_and_does_not_start_missing_prior_unit(self):
        systemd = FakeSystemd(start_failures=[True])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("fresh-failure")

        self.assertFalse((self.prefix / "current").exists())
        self.assertFalse((self.prefix / "personalities.json").exists())
        self.assertFalse((self.prefix / "scenes.json").exists())
        self.assertFalse((self.prefix / "sounds").exists())
        self.assertFalse(self.unit.exists())
        self.assertEqual(
            systemd.events,
            ["stop", "daemon-reload", "start-and-verify", "stop", "daemon-reload"],
        )

    def test_manual_rollback_restores_snapshot_and_refuses_second_attempt(self):
        self._legacy_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)
        result = deployer.deploy("manual-rollback")
        (self.state / "operator-settings.json").write_text("new settings", encoding="utf-8")
        (self.prefix / "scenes.json").write_text("new scenes", encoding="utf-8")

        rolled_back = deployer.rollback_last()

        self.assertTrue(rolled_back.rolled_back)
        self.assertEqual(rolled_back.backup_path, result.backup_path)
        self.assertFalse((self.prefix / "current").exists())
        self.assertEqual(self.unit.read_text(encoding="utf-8"), "legacy unit\n")
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(encoding="utf-8"),
            '{"version": 3}\n',
        )
        self.assertEqual(
            (self.prefix / "scenes.json").read_text(encoding="utf-8"),
            '{"scenes": ["custom"]}\n',
        )
        with self.assertRaisesRegex(DeploymentError, "not an active rollback candidate"):
            deployer.rollback_last()

    def test_stale_rollback_record_cannot_replace_a_different_current_release(self):
        old = self._old_versioned_install()
        deployer = self._deployer(FakeSystemd())
        deployer.deploy("new")
        other = self.prefix / "releases" / "other"
        other.mkdir()
        deployer._switch_current(other)

        with self.assertRaisesRegex(DeploymentError, "refusing stale rollback"):
            deployer.rollback_last()

        self.assertEqual((self.prefix / "current").resolve(), other.resolve())
        self.assertTrue(old.exists())

    def test_failed_manual_rollback_restores_the_active_release_and_newer_state(self):
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)
        active = deployer.deploy("active-release").release_path
        (self.state / "operator-settings.json").write_text(
            "new settings", encoding="utf-8"
        )
        (self.prefix / "scenes.json").write_text("new scenes", encoding="utf-8")
        systemd.start_failures.extend((True, False))

        with self.assertRaisesRegex(
            DeploymentError, "active release was restored"
        ):
            deployer.rollback_last()

        self.assertEqual((self.prefix / "current").resolve(), active.resolve())
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(encoding="utf-8"),
            "new settings",
        )
        self.assertEqual(
            (self.prefix / "scenes.json").read_text(encoding="utf-8"),
            "new scenes",
        )
        self.assertTrue(old.exists())
        record = json.loads(deployer.paths.record.read_text(encoding="utf-8"))
        self.assertEqual(record["status"], "active")

    def test_current_link_outside_managed_releases_is_rejected(self):
        self.prefix.mkdir(parents=True)
        outside = self.root / "outside"
        outside.mkdir()
        os.symlink(outside, self.prefix / "current")

        with self.assertRaisesRegex(DeploymentError, "must point inside"):
            self._deployer().preflight()

    def test_source_symlink_is_rejected_before_staging(self):
        target = self.source / "real-requirements.txt"
        target.write_text("", encoding="utf-8")
        (self.source / "requirements.txt").unlink()
        os.symlink(target.name, self.source / "requirements.txt")

        with self.assertRaisesRegex(DeploymentError, "cannot be a symlink"):
            self._deployer().preflight()

    def test_manifest_detects_post_staging_tamper(self):
        deployer = self._deployer()
        deployer.preflight()
        release = deployer._prepare_release("manifest-test")
        (release / "skeleton_all_in_one_mqtt.py").write_text(
            "VALUE = 2\n", encoding="utf-8"
        )

        with self.assertRaisesRegex(DeploymentError, "changed after staging"):
            deployer._verify_manifest(release)


class SystemdManagerTests(unittest.TestCase):
    @staticmethod
    def _completed(stdout=""):
        return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")

    def test_missing_unit_is_already_stopped_for_a_fresh_install(self):
        manager = SystemdManager()
        with mock.patch.object(
            manager,
            "run",
            return_value=self._completed("not-found\n"),
        ) as run:
            manager.stop()

        run.assert_called_once()

    def test_ready_active_running_process_passes_health_gate(self):
        manager = SystemdManager()
        results = [
            self._completed(),
            self._completed("active\n"),
            self._completed(
                "ActiveState=active\nSubState=running\nResult=success\n"
                "ExecMainStatus=0\nMainPID=123\nNRestarts=0\n"
            ),
        ]
        with mock.patch.object(manager, "run", side_effect=results):
            manager.start_and_verify(timeout=120, settle_seconds=0)

    def test_active_unit_without_main_process_fails_health_gate(self):
        manager = SystemdManager()
        results = [
            self._completed(),
            self._completed("active\n"),
            self._completed(
                "ActiveState=active\nSubState=running\nResult=success\n"
                "ExecMainStatus=0\nMainPID=0\nNRestarts=0\n"
            ),
        ]
        with mock.patch.object(manager, "run", side_effect=results):
            with self.assertRaisesRegex(DeploymentError, "no running main process"):
                manager.start_and_verify(timeout=120, settle_seconds=0)


if __name__ == "__main__":
    unittest.main()
