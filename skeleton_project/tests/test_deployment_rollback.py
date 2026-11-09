import json
import os
import stat
import unittest
from pathlib import Path
import tempfile
from unittest import mock

from holiday_skeleton.deployment import (
    DeploymentError,
    DeploymentPaths,
    ReleaseDeployer,
    SystemdManager,
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


class DeploymentRollbackTests(unittest.TestCase):
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
        os.chmod(self.unit, 0o644)
        os.chmod(self.prefix / "personalities.json", 0o644)
        os.chmod(self.prefix / "scenes.json", 0o640)
        os.chmod(self.prefix / "sounds", 0o750)
        os.chmod(self.prefix / "sounds" / "custom.wav", 0o640)
        os.chmod(self.state / "operator-settings.json", 0o600)
        os.chmod(self.state / "diagnostic-events.json", 0o640)

    def _old_versioned_install(self):
        self._legacy_install()
        old = self.prefix / "releases" / "old"
        old.mkdir(parents=True)
        (old / "old.txt").write_text("working", encoding="utf-8")
        os.symlink(Path("releases") / "old", self.prefix / "current")
        return old

    @staticmethod
    def _mode(path):
        return stat.S_IMODE(path.stat().st_mode)

    def _assert_legacy_metadata(self):
        self.assertEqual(self._mode(self.unit), 0o644)
        self.assertEqual(self._mode(self.prefix / "personalities.json"), 0o644)
        self.assertEqual(self._mode(self.prefix / "scenes.json"), 0o640)
        self.assertEqual(self._mode(self.prefix / "sounds"), 0o750)
        self.assertEqual(self._mode(self.prefix / "sounds" / "custom.wav"), 0o640)
        self.assertEqual(self._mode(self.state / "operator-settings.json"), 0o600)
        self.assertEqual(self._mode(self.state / "diagnostic-events.json"), 0o640)
        expected_owner = (os.getuid(), os.getgid())
        for path in (
            self.unit,
            self.prefix / "personalities.json",
            self.prefix / "scenes.json",
            self.prefix / "sounds",
            self.prefix / "sounds" / "custom.wav",
            self.state / "operator-settings.json",
            self.state / "diagnostic-events.json",
        ):
            with self.subTest(path=path):
                status = path.stat()
                self.assertEqual((status.st_uid, status.st_gid), expected_owner)

    # --- Failed deployment triggers automatic rollback ---

    def test_failed_deployment_rolls_back_to_previous_release(self):
        """A failed deployment triggers automatic rollback and restores the
        previous release, unit file, state, and shared content."""
        old = self._old_versioned_install()
        systemd = FakeSystemd(start_failures=[True, False])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("bad-release")

        # current is restored to the previous versioned release.
        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        # Unit file restored.
        self.assertEqual(self.unit.read_text(encoding="utf-8"), "legacy unit\n")
        # State and shared content restored.
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(), '{"version": 3}\n'
        )
        self.assertEqual(
            (self.prefix / "scenes.json").read_text(), '{"scenes": ["custom"]}\n'
        )
        # No active record exists after a rolled-back deploy.
        self.assertFalse(deployer.paths.record.exists())
        # Candidate release is kept in place for inspection.
        self.assertTrue((self.prefix / "releases" / "bad-release").exists())
        # Metadata preserved.
        self._assert_legacy_metadata()

    def test_failed_health_check_rolls_back_and_restarts_previous(self):
        """After rollback, the prior release is restarted and verified."""
        old = self._old_versioned_install()
        systemd = FakeSystemd(start_failures=[True, False])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("failing-health")

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        # Verify the rollback transaction stopped, restored, and restarted.
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

    def test_failed_deployment_with_simulate_activation_failure_rolls_back(self):
        """Simulate a deliberate post-switch activation failure and verify
        automatic rollback restores the prior release."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(
            DeploymentError, "operator-requested activation failure"
        ):
            deployer.deploy("acceptance-fault", simulate_activation_failure=True)

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(self.unit.read_text(encoding="utf-8"), "legacy unit\n")
        self._assert_legacy_metadata()
        self.assertEqual(
            systemd.events,
            ["stop", "daemon-reload", "stop", "daemon-reload", "start-and-verify"],
        )

    def test_fresh_install_failure_cleans_up_and_does_not_start_missing_unit(self):
        """A failed fresh install removes seeded content and does not start a
        nonexistent prior unit."""
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

    def test_verify_rollback_passes_after_rollback_restores_previous(self):
        """After a rollback, verify_rollback confirms the prior release is
        the active release and the backup is intact."""
        old = self._old_versioned_install()
        systemd = FakeSystemd(start_failures=[True, False])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("verify-rollback")

        backup = next(
            (deployer.paths.backups / d).resolve()
            for d in os.listdir(deployer.paths.backups)
        )
        # Should not raise.
        deployer.verify_rollback(old.resolve(), backup)

    def test_verify_rollback_detects_mismatched_current(self):
        """verify_rollback raises when current does not match the expected
        previous release."""
        old = self._old_versioned_install()
        systemd = FakeSystemd(start_failures=[True, False])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("verify-mismatch")

        # Switch current to something other than the rollback target.
        other = self.prefix / "releases" / "other"
        other.mkdir()
        deployer._switch_current(other)

        backup = next(
            (deployer.paths.backups / d).resolve()
            for d in os.listdir(deployer.paths.backups)
        )
        with self.assertRaisesRegex(
            DeploymentError, "rollback verification failed"
        ):
            deployer.verify_rollback(old.resolve(), backup)

    def test_verify_rollback_detects_missing_backup(self):
        """verify_rollback raises when the backup path does not exist."""
        old = self._old_versioned_install()
        systemd = FakeSystemd(start_failures=[True, False])
        deployer = self._deployer(systemd)

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("verify-no-backup")

        missing = self.prefix / "releases" / "does-not-exist"
        with self.assertRaisesRegex(
            DeploymentError, "does not exist"
        ):
            deployer.verify_rollback(old.resolve(), missing)

    # --- Edge case: partial deployment ---

    def test_partial_deployment_cleans_up_staged_release(self):
        """When staging fails midway, the partial release is removed and the
        existing release is untouched."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        with mock.patch(
            "holiday_skeleton.deployment.shutil.copy2",
            side_effect=PermissionError("target is unwritable"),
        ) as copy:
            with self.assertRaisesRegex(
                DeploymentError, "cannot stage release files"
            ):
                deployer.deploy("unwritable-target")
        copy.assert_called_once()

        # Existing release unchanged.
        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual((old / "old.txt").read_text(), "working")
        self.assertEqual(self.unit.read_text(), "legacy unit\n")
        # No new release was created.
        self.assertFalse((self.prefix / "releases" / "unwritable-target").exists())
        # No systemd events were triggered.
        self.assertEqual(systemd.events, [])

    def test_partial_deployment_from_backup_stage_restores_transaction(self):
        """When the deployment fails after backup creation but before the
        link switch, the transaction restores state."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        def fail_seed(_release):
            (self.state / "operator-settings.json").write_text(
                "changed", encoding="utf-8"
            )
            raise DeploymentError("simulated content activation error")

        deployer._seed_shared_content = fail_seed

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("seed-failure")

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(),
            '{"version": 3}\n',
        )

    def test_partial_deployment_staging_dir_removed_on_failure(self):
        """When staging fails, the staging directory is cleaned up and the
        release is not created."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        def fail_verify(_release):
            raise DeploymentError("manifest verification failed")

        deployer._verify_manifest = fail_verify

        with self.assertRaisesRegex(DeploymentError, "manifest verification failed"):
            deployer.deploy("manifest-fail")

        # No release directory should exist (staging was cleaned up).
        self.assertFalse((self.prefix / "releases" / "manifest-fail").exists())
        # Existing release is untouched.
        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        # No systemd events were triggered (failure is before backup).
        self.assertEqual(systemd.events, [])

    # --- Edge case: permission errors ---

    def test_permission_error_during_backup_triggers_rollback(self):
        """A permission error during backup creation is caught and the
        deployment fails before activation."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        def fail_backup(_release_id, _previous, _release):
            raise PermissionError("cannot write backup directory")

        deployer._create_backup = fail_backup

        with self.assertRaisesRegex(
            DeploymentError, "deployment stopped before activation"
        ):
            deployer.deploy("perm-backup")

        # Existing release untouched.
        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(self.unit.read_text(), "legacy unit\n")

    def test_permission_error_during_seed_triggers_rollback(self):
        """A permission error during seeding shared content triggers the
        rollback transaction."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        def fail_seed(_release):
            raise PermissionError("cannot write to prefix")

        deployer._seed_shared_content = fail_seed

        with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
            deployer.deploy("perm-seed")

        # Rollback restored the prior release.
        self.assertEqual((self.prefix / "current").resolve(), old.resolve())
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(),
            '{"version": 3}\n',
        )

    def test_permission_error_during_unit_write_triggers_rollback(self):
        """A permission error writing the systemd unit triggers rollback."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        original_atomic_write = __import__(
            "holiday_skeleton.deployment", fromlist=["_atomic_write"
        ])._atomic_write

        call_count = {"unit": 0}

        def fail_unit_write(path, data, mode=0o600):
            if path == self.unit and call_count["unit"] == 0:
                call_count["unit"] = 1
                raise PermissionError("cannot write unit file")
            original_atomic_write(path, data, mode)

        with mock.patch(
            "holiday_skeleton.deployment._atomic_write",
            side_effect=fail_unit_write,
        ):
            with self.assertRaisesRegex(DeploymentError, "rolled back successfully"):
                deployer.deploy("perm-unit")

        self.assertEqual((self.prefix / "current").resolve(), old.resolve())

    def test_permission_error_in_rollback_transaction_reports_combined_error(self):
        """When the rollback transaction itself fails due to a permission
        error, the combined error is reported."""
        old = self._old_versioned_install()
        # start_failures=[True] makes the deploy's start_and_verify fail,
        # triggering the rollback path.
        systemd = FakeSystemd(start_failures=[True])
        deployer = self._deployer(systemd)

        # Backup succeeds, but restoring the backup fails with a permission
        # error inside the rollback transaction.
        def fail_restore(backup):
            raise PermissionError("cannot restore backup file")

        deployer._restore_backup = fail_restore

        with self.assertRaisesRegex(
            DeploymentError,
            "deployment failed.*automatic rollback also failed",
        ):
            deployer.deploy("perm-rollback")

        # The rollback failed, so the candidate is stopped but the prior
        # release was not restored.
        self.assertFalse(deployer.paths.record.exists())

    def test_permission_error_stopping_service_blocks_rollback(self):
        """When stopping the service during rollback raises a DeploymentError,
        rollback is not attempted and the error is reported."""
        old = self._old_versioned_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)

        # Make _switch_current fail with simulate_activation_failure=True so
        # the deploy raises after backup, then make the rollback's stop()
        # raise.
        def fail_stop(_timeout=30.0):
            raise DeploymentError("cannot stop service: permission denied")

        # Patch the systemd stop method so the rollback transaction's stop
        # raises.
        def patched_stop(_timeout=30.0):
            # First call is the deploy's stop, second is rollback's stop.
            raise DeploymentError("cannot stop service: permission denied")

        # Instead of patching, directly test the rollback transaction.
        backup = deployer._create_backup("test", old, self.prefix / "releases" / "test")
        with mock.patch.object(systemd, "stop", side_effect=fail_stop):
            with self.assertRaisesRegex(
                DeploymentError, "cannot stop the candidate safely"
            ):
                deployer._rollback_transaction(old, backup)

    # --- Manual rollback (rollback_last) edge cases ---

    def test_manual_rollback_restores_snapshot(self):
        """rollback_last restores the backup and marks the record as
        rolled_back."""
        self._legacy_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)
        result = deployer.deploy("manual-rollback")
        (self.state / "operator-settings.json").write_text(
            "new settings", encoding="utf-8"
        )
        (self.prefix / "scenes.json").write_text("new scenes", encoding="utf-8")

        rolled_back = deployer.rollback_last()

        self.assertTrue(rolled_back.rolled_back)
        self.assertEqual(rolled_back.backup_path, result.backup_path)
        self.assertFalse((self.prefix / "current").exists())
        self.assertEqual(self.unit.read_text(encoding="utf-8"), "legacy unit\n")
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(),
            '{"version": 3}\n',
        )
        self.assertEqual(
            (self.prefix / "scenes.json").read_text(),
            '{"scenes": ["custom"]}\n',
        )

    def test_manual_rollback_refuses_second_attempt(self):
        """After a manual rollback, a second attempt is refused because the
        record is no longer 'active'."""
        self._legacy_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)
        deployer.deploy("rollback-twice")

        deployer.rollback_last()

        with self.assertRaisesRegex(
            DeploymentError, "not an active rollback candidate"
        ):
            deployer.rollback_last()

    def test_manual_rollback_without_record_refuses(self):
        """rollback_last without a deployment record raises."""
        self._legacy_install()
        with self.assertRaisesRegex(
            DeploymentError, "cannot read last deployment record"
        ):
            self._deployer().rollback_last()

    def test_manual_rollback_restores_state_changed_after_deploy(self):
        """rollback_last restores state that changed after the deploy."""
        self._legacy_install()
        systemd = FakeSystemd()
        deployer = self._deployer(systemd)
        deployer.deploy("changed-after")
        (self.state / "operator-settings.json").write_text(
            "changed", encoding="utf-8"
        )

        rolled_back = deployer.rollback_last()

        self.assertTrue(rolled_back.rolled_back)
        self.assertEqual(
            (self.state / "operator-settings.json").read_text(),
            '{"version": 3}\n',
        )
        record = json.loads(deployer.paths.record.read_text())
        self.assertEqual(record["status"], "rolled_back")

    def test_stale_rollback_record_cannot_replace_different_current(self):
        """A stale rollback record cannot replace a different current release."""
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

    # --- Manual rollback failure restores the active release ---

    def test_failed_manual_rollback_restores_active_release(self):
        """When manual rollback fails, the active release is restored."""
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
            (self.state / "operator-settings.json").read_text(),
            "new settings",
        )
        self.assertEqual(
            (self.prefix / "scenes.json").read_text(),
            "new scenes",
        )
        record = json.loads(deployer.paths.record.read_text())
        self.assertEqual(record["status"], "active")


if __name__ == "__main__":
    unittest.main()
