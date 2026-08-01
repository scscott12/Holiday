import tempfile
import unittest
from pathlib import Path
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.health import ComponentState
from holiday_skeleton.settings import (
    DayProfile,
    OperatorSettings,
    OperatorSettingsStore,
    SettingsConfigError,
)


class RecordingStore:
    def __init__(self, error=None):
        self.error = error
        self.saved = []

    def save(self, settings):
        if self.error is not None:
            raise self.error
        self.saved.append(settings)
        return OperatorSettings(**{
            **settings.__dict__,
            "updated_at": "2026-08-01T12:00:00+00:00",
        })


class RuntimeSettingsTests(unittest.TestCase):
    def persisted(self):
        return OperatorSettings(
            personality="graveyard_host",
            motion_enabled=False,
            idle_life_enabled=False,
            night_mode=True,
            eyes_dim=0.05,
            eyes_full=0.4,
            volume=0.5,
            day_profile=DayProfile(eyes_dim=0.2, eyes_full=0.8, volume=1.0),
        )

    def test_startup_restores_exact_night_values_and_day_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-settings.json"
            OperatorSettingsStore(path).save(self.persisted())
            published = []
            with mock.patch.multiple(
                runtime,
                PERSIST_SETTINGS_ENABLED=True,
                PERSIST_SETTINGS_PATH=str(path),
                PERSONALITY_REQUESTED="pirate",
                motion_enabled=True,
                IDLE_LIFE_ENABLED=True,
                night_mode=False,
                EYES_LISTEN_FRAC=0.18,
                EYES_SPEAK_FRAC=1.0,
                VOLUME=1.0,
                _day={"listen": None, "speak": None, "vol": None},
                _settings_store=None,
                _settings_loaded=None,
                _health_set=mock.DEFAULT,
                mqtt_pub=lambda topic, payload, retain=False: published.append((topic, payload)),
            ) as patched:
                runtime._init_persistent_settings()

                self.assertEqual(runtime.PERSONALITY_REQUESTED, "graveyard_host")
                self.assertFalse(runtime.motion_enabled)
                self.assertFalse(runtime.IDLE_LIFE_ENABLED)
                self.assertTrue(runtime.night_mode)
                self.assertEqual(runtime.EYES_LISTEN_FRAC, 0.05)
                self.assertEqual(runtime.EYES_SPEAK_FRAC, 0.4)
                self.assertEqual(runtime.VOLUME, 0.5)
                self.assertEqual(runtime._day["listen"], 0.2)
                self.assertEqual(runtime._day["speak"], 0.8)
                self.assertEqual(runtime._day["vol"], 1.0)
                self.assertEqual(runtime._settings_state, "restored")
                self.assertIn(("settings/state", "restored"), published)
                patched["_health_set"].assert_any_call(
                    "settings",
                    ComponentState.READY,
                    mock.ANY,
                )

                runtime._settings_store = None
                runtime._toggle_night_mode("OFF")
                self.assertFalse(runtime.night_mode)
                self.assertEqual(runtime.EYES_LISTEN_FRAC, 0.2)
                self.assertEqual(runtime.EYES_SPEAK_FRAC, 0.8)
                self.assertEqual(runtime.VOLUME, 1.0)

    def test_operator_change_saves_complete_snapshot(self):
        store = RecordingStore()
        with mock.patch.multiple(
            runtime,
            _settings_store=store,
            _personality_active=None,
            motion_enabled=True,
            IDLE_LIFE_ENABLED=True,
            night_mode=False,
            EYES_LISTEN_FRAC=0.18,
            EYES_SPEAK_FRAC=1.0,
            VOLUME=1.0,
            _day={"listen": None, "speak": None, "vol": None},
            _health_set=mock.DEFAULT,
            mqtt_pub=mock.DEFAULT,
        ):
            runtime._set_volume(1.25)

            self.assertEqual(len(store.saved), 1)
            snapshot = store.saved[0]
            self.assertEqual(snapshot.personality, "legacy")
            self.assertEqual(snapshot.volume, 1.25)
            self.assertEqual(snapshot.day_profile.volume, 1.25)
            self.assertEqual(runtime._settings_state, "saved")

    def test_save_failure_degrades_settings_only_and_keeps_live_change(self):
        store = RecordingStore(SettingsConfigError("disk is read-only"))
        with mock.patch.multiple(
            runtime,
            _settings_store=store,
            night_mode=False,
            VOLUME=1.0,
            _day={"listen": None, "speak": None, "vol": None},
            _health_set=mock.DEFAULT,
            mqtt_pub=mock.DEFAULT,
        ) as patched:
            runtime._set_volume(0.75)

            self.assertEqual(runtime.VOLUME, 0.75)
            self.assertEqual(runtime._settings_state, "error")
            self.assertEqual(runtime._settings_last_error, "disk is read-only")
            patched["_health_set"].assert_called_with(
                "settings",
                ComponentState.DEGRADED,
                "disk is read-only",
            )

    def test_non_finite_control_value_is_ignored_without_a_write(self):
        store = RecordingStore()
        with mock.patch.multiple(
            runtime,
            _settings_store=store,
            VOLUME=1.0,
            mqtt_pub=mock.DEFAULT,
        ):
            runtime._set_volume(float("nan"))

            self.assertEqual(runtime.VOLUME, 1.0)
            self.assertEqual(store.saved, [])

    def test_invalid_saved_file_uses_defaults_without_stopping_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "operator-settings.json"
            path.write_text("{broken", encoding="utf-8")
            with mock.patch.multiple(
                runtime,
                PERSIST_SETTINGS_ENABLED=True,
                PERSIST_SETTINGS_PATH=str(path),
                PERSONALITY_REQUESTED="pirate",
                motion_enabled=True,
                _settings_store=None,
                _settings_loaded=None,
                _health_set=mock.DEFAULT,
                mqtt_pub=mock.DEFAULT,
            ):
                runtime._init_persistent_settings()

                self.assertEqual(runtime.PERSONALITY_REQUESTED, "pirate")
                self.assertTrue(runtime.motion_enabled)
                self.assertEqual(runtime._settings_state, "error")
                self.assertIn("cannot parse", runtime._settings_last_error)


if __name__ == "__main__":
    unittest.main()
