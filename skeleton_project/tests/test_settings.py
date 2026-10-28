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

    def test_requires_every_field_in_each_object(self):
        for path in ((), ("settings",), ("settings", "day_profile"),
                     ("settings", "calibration")):
            fields = sample_settings().to_payload()
            for key in path:
                fields = fields[key]
            for field in fields:
                with self.subTest(path=path, field=field):
                    payload = sample_settings().to_payload()
                    target = payload
                    for key in path:
                        target = target[key]
                    del target[field]
                    with self.assertRaisesRegex(
                        SettingsConfigError, f"missing fields: {field}"
                    ):
                        settings_from_payload(payload)

    def test_rejects_non_objects_and_unknown_fields_at_each_level(self):
        for path in ((), ("settings",), ("settings", "day_profile"),
                     ("settings", "calibration")):
            for value in (None, [], "object", 1, True, {"unexpected": 1}):
                with self.subTest(path=path, value=value):
                    payload = sample_settings().to_payload()
                    if path:
                        target = payload
                        for key in path[:-1]:
                            target = target[key]
                        target[path[-1]] = value
                    else:
                        payload = value
                    message = (
                        "unknown fields: unexpected" if isinstance(value, dict)
                        else "must be an object"
                    )
                    with self.assertRaisesRegex(SettingsConfigError, message):
                        settings_from_payload(payload)

    def test_numeric_bounds_and_invalid_values(self):
        levels = (("eyes_dim", 0, 1), ("eyes_full", 0, 1), ("volume", 0, 2))
        hardware = (
            ("jaw_rest", 0, 0.7),
            ("jaw_max", 0.1, 1),
            ("microphone_gate", 25, 5000),
            ("pir_hold_seconds", 0.1, 5),
            ("pir_cooldown_seconds", 1, 120),
        )
        for section, bounds in ((None, levels), ("day_profile", levels),
                                ("calibration", hardware)):
            for field, minimum, maximum in bounds:
                cases = [(value, None) for value in (minimum, maximum)]
                cases += [(value, "must be a number") for value in
                          (None, True, False, "0.5", [], {})]
                cases += [(value, f"must be between {minimum:g} and {maximum:g}")
                          for value in (minimum - 0.01, maximum + 0.01,
                                        float("nan"), float("inf"), float("-inf"))]
                for value, message in cases:
                    with self.subTest(section=section, field=field, value=value):
                        payload = sample_settings().to_payload()
                        target = payload["settings"]
                        if section:
                            target = target[section]
                        # Keep the paired jaw endpoint valid at either bound.
                        if field == "jaw_max":
                            target["jaw_rest"] = 0
                        target[field] = value
                        if message:
                            with self.assertRaisesRegex(
                                SettingsConfigError, f"{field} {message}"
                            ):
                                settings_from_payload(payload)
                        else:
                            restored = settings_from_payload(payload)
                            if section:
                                restored = getattr(restored, section)
                            self.assertEqual(getattr(restored, field), value)

    def test_boolean_fields_require_actual_booleans(self):
        for field in ("motion_enabled", "idle_life_enabled", "night_mode",
                      "maintenance_mode"):
            for value in (None, 0, 1, "true", "false", [], {}):
                with self.subTest(field=field, value=value):
                    payload = sample_settings().to_payload()
                    payload["settings"][field] = value
                    with self.assertRaisesRegex(
                        SettingsConfigError, f"{field} must be true or false"
                    ):
                        settings_from_payload(payload)

    def test_rejects_invalid_metadata(self):
        cases = (
            ("version", (None, False, "3", [], {}, 0, -1, 3.5),
             "unsupported settings version"),
            ("updated_at", (None, True, 1, [], {}, "", "x" * 65),
             "short timestamp"),
        )
        for field, values, message in cases:
            for value in values:
                with self.subTest(field=field, value=value):
                    payload = sample_settings().to_payload()
                    payload[field] = value
                    with self.assertRaisesRegex(SettingsConfigError, message):
                        settings_from_payload(payload)

    def test_personality_name_limits_and_invalid_values(self):
        for name in ("a", "a" * 64, "0_host-name"):
            with self.subTest(name=name):
                payload = sample_settings(personality=name).to_payload()
                self.assertEqual(settings_from_payload(payload).personality, name)
        for name in (None, 1, True, [], {}, "", "a" * 65, "Host", "host name", "host\n"):
            with self.subTest(name=name):
                payload = sample_settings(personality=name).to_payload()
                with self.assertRaisesRegex(SettingsConfigError, "safe lowercase name"):
                    settings_from_payload(payload)

    def test_rejects_empty_and_invalid_utf8_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            for raw in (b"", b"\xff"):
                with self.subTest(raw=raw):
                    path.write_bytes(raw)
                    with self.assertRaisesRegex(SettingsConfigError, "cannot parse"):
                        OperatorSettingsStore(path).load()

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
