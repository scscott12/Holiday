import os
import tempfile
import unittest
from pathlib import Path

from holiday_skeleton.calibration import HardwareCalibration

from holiday_skeleton.settings import (
    DayProfile,
    MAX_SETTINGS_BYTES,
    OperatorSettings,
    OperatorSettingsStore,
    SettingsConfigError,
    settings_from_payload,
)


def sample_settings(**overrides):
    values = {
        "personality": "graveyard_host",
        "motion_enabled": True,
        "idle_life_enabled": False,
        "night_mode": True,
        "eyes_dim": 0.06,
        "eyes_full": 0.48,
        "volume": 0.54,
        "day_profile": DayProfile(eyes_dim=0.18, eyes_full=0.8, volume=0.9),
        "maintenance_mode": True,
        "calibration": HardwareCalibration(
            jaw_rest=0.2,
            jaw_max=0.9,
            eyes_inverted=True,
            microphone_gate=360,
            pir_hold_seconds=1.1,
            pir_cooldown_seconds=12,
        ),
    }
    values.update(overrides)
    return OperatorSettings(**values)


class OperatorSettingsStoreTests(unittest.TestCase):
    def test_missing_file_has_no_saved_override(self):
        with tempfile.TemporaryDirectory() as directory:
            store = OperatorSettingsStore(Path(directory) / "settings.json")
            self.assertIsNone(store.load())

    def test_round_trip_is_atomic_private_and_preserves_night_day_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state" / "operator-settings.json"
            store = OperatorSettingsStore(path)

            saved = store.save(sample_settings())
            loaded = store.load()

            self.assertEqual(loaded, saved)
            self.assertNotEqual(saved.updated_at, "never")
            self.assertEqual(loaded.day_profile.volume, 0.9)
            self.assertEqual(loaded.volume, 0.54)
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_second_save_replaces_complete_document(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-settings.json"
            store = OperatorSettingsStore(path)
            store.save(sample_settings(personality="pirate"))

            store.save(sample_settings(personality="silent_watcher", volume=0.2))

            loaded = store.load()
            self.assertEqual(loaded.personality, "silent_watcher")
            self.assertEqual(loaded.volume, 0.2)
            self.assertNotIn("pirate", path.read_text(encoding="utf-8"))

    def test_serialized_document_contains_only_operator_controls(self):
        payload = sample_settings().to_payload()

        self.assertEqual(
            set(payload["settings"]),
            {
                "personality",
                "motion_enabled",
                "idle_life_enabled",
                "night_mode",
                "maintenance_mode",
                "eyes_dim",
                "eyes_full",
                "volume",
                "day_profile",
                "calibration",
            },
        )
        serialized = str(payload).lower()
        for forbidden in ("transcript", "visitor", "prompt", "password", "mqtt"):
            self.assertNotIn(forbidden, serialized)

    def test_rejects_incompatible_unknown_and_missing_fields(self):
        for version in (4, True):
            with self.subTest(version=version):
                payload = sample_settings().to_payload()
                payload["version"] = version
                with self.assertRaisesRegex(
                    SettingsConfigError,
                    "unsupported settings version",
                ):
                    settings_from_payload(payload)

        payload = sample_settings().to_payload()
        payload["settings"]["secret"] = "do not persist this"
        with self.assertRaisesRegex(SettingsConfigError, "unknown fields: secret"):
            settings_from_payload(payload)

        payload = sample_settings().to_payload()
        payload["settings"].pop("motion_enabled")
        with self.assertRaisesRegex(SettingsConfigError, "missing fields: motion_enabled"):
            settings_from_payload(payload)

    def test_version_one_file_migrates_with_maintenance_unlocked(self):
        payload = sample_settings().to_payload()
        payload["version"] = 1
        payload["settings"].pop("maintenance_mode")
        payload["settings"].pop("calibration")

        restored = settings_from_payload(payload)

        self.assertFalse(restored.maintenance_mode)
        self.assertEqual(restored.personality, "graveyard_host")
        self.assertEqual(restored.calibration, HardwareCalibration())

    def test_version_two_file_uses_configured_hardware_defaults(self):
        payload = sample_settings().to_payload()
        payload["version"] = 2
        payload["settings"].pop("calibration")
        configured = HardwareCalibration(
            jaw_rest=0.3,
            jaw_max=0.8,
            eyes_inverted=True,
            microphone_gate=450,
            pir_hold_seconds=1.5,
            pir_cooldown_seconds=20,
        )

        restored = settings_from_payload(payload, configured)

        self.assertEqual(restored.calibration, configured)

    def test_rejects_invalid_names_types_ranges_and_non_finite_numbers(self):
        cases = (
            ("personality", "../escape", "safe lowercase name"),
            ("motion_enabled", 1, "true or false"),
            ("maintenance_mode", "yes", "true or false"),
            ("eyes_dim", -0.1, "between 0 and 1"),
            ("eyes_full", float("nan"), "between 0 and 1"),
            ("volume", 2.1, "between 0 and 2"),
        )
        for field, value, message in cases:
            with self.subTest(field=field):
                payload = sample_settings().to_payload()
                payload["settings"][field] = value
                with self.assertRaisesRegex(SettingsConfigError, message):
                    settings_from_payload(payload)

    def test_rejects_malformed_and_oversized_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-settings.json"
            store = OperatorSettingsStore(path)
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(SettingsConfigError, "cannot parse"):
                store.load()

            path.write_bytes(b"x" * (MAX_SETTINGS_BYTES + 1))
            with self.assertRaisesRegex(SettingsConfigError, "exceed"):
                store.load()

    def test_failed_replace_does_not_destroy_previous_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-settings.json"
            store = OperatorSettingsStore(path)
            store.save(sample_settings(personality="pirate"))
            original = path.read_bytes()

            original_replace = os.replace

            def fail_replace(source, destination):
                if Path(destination) == path:
                    raise OSError("simulated power-safe write failure")
                return original_replace(source, destination)

            from unittest import mock

            with mock.patch("holiday_skeleton.settings.os.replace", fail_replace):
                with self.assertRaisesRegex(SettingsConfigError, "cannot save"):
                    store.save(sample_settings(personality="silent_watcher"))

            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(list(path.parent.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
