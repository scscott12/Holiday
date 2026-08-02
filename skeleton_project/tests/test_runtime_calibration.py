import unittest
from types import SimpleNamespace
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.calibration import (
    CalibrationSession,
    CalibrationStep,
    CalibrationValues,
    HardwareCalibration,
)
from holiday_skeleton.controller import EventKind, SkeletonController
from holiday_skeleton.settings import OperatorSettings


class RecordingStore:
    def __init__(self, error=None):
        self.error = error
        self.saved = []

    def save(self, settings):
        if self.error is not None:
            raise self.error
        self.saved.append(settings)
        return OperatorSettings(
            **{**settings.__dict__, "updated_at": "2026-08-02T00:00:00+00:00"}
        )


def session_with(values=None):
    session = CalibrationSession()
    session.start(values or CalibrationValues(
        hardware=HardwareCalibration(),
        eyes_dim=0.18,
        eyes_full=1.0,
        speaker_volume=1.0,
    ))
    return session


class RuntimeCalibrationTests(unittest.TestCase):
    def base_patch(self, **overrides):
        values = {
            "_calibration_session": CalibrationSession(),
            "_calibration_state": "ready",
            "_calibration_last_result": "ready",
            "_calibration_last_error": "none",
            "_calibration_last_preview": "none",
            "_calibration_last_saved": "never",
            "_calibration_save_count": 0,
            "_calibration_preview_count": 0,
            "_calibration_volume_override": None,
            "CALIBRATION_ENABLED": True,
            "maintenance_mode": True,
            "night_mode": False,
            "_settings_store": RecordingStore(),
            "_health_set": mock.DEFAULT,
            "mqtt_pub": mock.DEFAULT,
        }
        values.update(overrides)
        return mock.patch.multiple(runtime, **values)

    def test_start_requires_maintenance_and_day_profile(self):
        with self.base_patch(maintenance_mode=False):
            runtime._handle_calibration_command({"action": "start"})
            self.assertFalse(runtime._calibration_session.active)
            self.assertIn("Maintenance Mode", runtime._calibration_last_error)

        with self.base_patch(night_mode=True):
            runtime._handle_calibration_command({"action": "start"})
            self.assertFalse(runtime._calibration_session.active)
            self.assertIn("Night Mode", runtime._calibration_last_error)

    def test_start_snapshots_current_values_without_moving_hardware(self):
        with self.base_patch(
            JAW_REST_FRAC=0.3,
            JAW_MAX_FRAC=0.8,
            EYES_INVERT=1,
            ENERGY_GATE=300.0,
            MOTION_HOLD_SEC=1.1,
            MOTION_COOLDOWN_SEC=12.0,
            EYES_LISTEN_FRAC=0.2,
            EYES_SPEAK_FRAC=0.7,
            VOLUME=0.9,
            _calibration_raw_jaw=mock.DEFAULT,
            _calibration_raw_eyes=mock.DEFAULT,
        ) as patched:
            runtime._handle_calibration_command({"action": "start"})

            self.assertTrue(runtime._calibration_session.active)
            self.assertEqual(runtime._calibration_session.staged.hardware.jaw_rest, 0.3)
            self.assertEqual(runtime._calibration_session.staged.eyes_full, 0.7)
            patched["_calibration_raw_jaw"].assert_not_called()
            patched["_calibration_raw_eyes"].assert_not_called()

    def test_number_topic_stages_percent_without_applying_live_value(self):
        controller = SkeletonController(lambda _event: None)
        session = session_with()
        message = SimpleNamespace(
            topic="holiday/skeleton/calibration/jaw_rest/set",
            payload=b"30",
        )
        with self.base_patch(
            controller=controller,
            _calibration_session=session,
            JAW_REST_FRAC=0.25,
            _enqueue=mock.DEFAULT,
        ) as patched:
            runtime._on_message(None, None, message)

            patched["_enqueue"].assert_called_once_with(
                EventKind.CALIBRATION_COMMAND,
                {"action": "set", "step": "jaw_rest", "value": 0.3},
            )
            self.assertEqual(runtime.JAW_REST_FRAC, 0.25)

    def test_invalid_non_finite_mqtt_value_is_not_queued(self):
        message = SimpleNamespace(
            topic="holiday/skeleton/calibration/microphone_gate/set",
            payload=b"nan",
        )
        with self.base_patch(_enqueue=mock.DEFAULT) as patched:
            runtime._on_message(None, None, message)

            patched["_enqueue"].assert_not_called()
            self.assertEqual(runtime._calibration_last_result, "error")

    def test_jaw_preview_moves_once_and_always_restores_safe_output(self):
        session = session_with()
        jaw = SimpleNamespace(fraction=None)
        with self.base_patch(
            _calibration_session=session,
            _jaw=jaw,
            JAW_REST_FRAC=0.25,
            CALIBRATION_PREVIEW_SEC=0.0,
            controller=None,
        ):
            result = runtime._preview_calibration()

            self.assertIn("jaw rest", result)
            self.assertEqual(jaw.fraction, 0.25)

    def test_eye_preview_is_capped_and_restores_physical_off(self):
        session = session_with(CalibrationValues(
            hardware=HardwareCalibration(eyes_inverted=True),
            eyes_dim=0.2,
            eyes_full=0.9,
            speaker_volume=1.0,
        ))
        session.select(CalibrationStep.EYES_FULL)
        eyes = SimpleNamespace(duty_cycle=None)
        writes = []

        class Eyes:
            @property
            def duty_cycle(self):
                return writes[-1] if writes else None

            @duty_cycle.setter
            def duty_cycle(self, value):
                writes.append(value)

        eyes = Eyes()
        with self.base_patch(
            _calibration_session=session,
            _eyes_ch=eyes,
            EYES_INVERT=0,
            CALIBRATION_PREVIEW_SEC=0.0,
            controller=None,
        ):
            result = runtime._preview_calibration()

            self.assertIn("capped at 35%", result)
            self.assertEqual(writes[0], int(0xFFFF * 0.65))
            self.assertEqual(writes[-1], 0)

    def test_microphone_preview_reports_observed_and_recommended_gate(self):
        session = session_with()
        session.select(CalibrationStep.MICROPHONE_GATE)
        with self.base_patch(
            _calibration_session=session,
            _calibration_sample_microphone=mock.Mock(return_value=(120.0, 236.0)),
        ):
            result = runtime._preview_calibration()

            self.assertIn("ambient RMS p95 120", result)
            self.assertIn("recommended gate 236", result)

    def test_speaker_preview_uses_staged_volume_and_keeps_jaw_interlocked(self):
        session = session_with(CalibrationValues(
            hardware=HardwareCalibration(),
            eyes_dim=0.18,
            eyes_full=1.0,
            speaker_volume=1.4,
        ))
        session.select(CalibrationStep.SPEAKER_VOLUME)

        class Engine:
            def speak(self, text, stop_event=None):
                self.observed_volume = runtime._speech_volume()
                return SimpleNamespace(frames_written=10)

        engine = Engine()
        with self.base_patch(
            _calibration_session=session,
            _speech_engine=engine,
            controller=None,
        ):
            result = runtime._preview_calibration()

            self.assertEqual(engine.observed_volume, 1.4)
            self.assertIn("140%", result)
            self.assertIsNone(runtime._calibration_volume_override)

    def test_successful_save_persists_complete_snapshot_then_applies_live(self):
        session = session_with()
        session.update(CalibrationStep.JAW_REST, 0.3)
        session.update(CalibrationStep.JAW_MAX, 0.85)
        session.update(CalibrationStep.EYES_FULL, 0.75)
        session.update(CalibrationStep.SPEAKER_VOLUME, 1.2)
        store = RecordingStore()
        engine = SimpleNamespace(rest_fraction=0.25, maximum_fraction=1.0)
        with self.base_patch(
            _calibration_session=session,
            _settings_store=store,
            _speech_engine=engine,
            JAW_REST_FRAC=0.25,
            JAW_MAX_FRAC=1.0,
            EYES_LISTEN_FRAC=0.18,
            EYES_SPEAK_FRAC=1.0,
            VOLUME=1.0,
            _personality_active=None,
            motion_enabled=True,
            IDLE_LIFE_ENABLED=True,
            _day={"listen": None, "speak": None, "vol": None},
            _calibration_safe_outputs=mock.DEFAULT,
            _publish_operator_controls=mock.DEFAULT,
        ):
            runtime._handle_calibration_command({"action": "save"})

            self.assertEqual(len(store.saved), 1)
            self.assertEqual(store.saved[0].calibration.jaw_rest, 0.3)
            self.assertEqual(store.saved[0].eyes_full, 0.75)
            self.assertEqual(runtime.JAW_REST_FRAC, 0.3)
            self.assertEqual(runtime.VOLUME, 1.2)
            self.assertEqual(engine.rest_fraction, 0.3)
            self.assertFalse(runtime._calibration_session.active)
            self.assertEqual(runtime._calibration_last_result, "saved")

    def test_failed_save_keeps_live_configuration_and_session_staged(self):
        session = session_with()
        session.update(CalibrationStep.JAW_REST, 0.3)
        store = RecordingStore(RuntimeError("disk read-only"))
        with self.base_patch(
            _calibration_session=session,
            _settings_store=store,
            JAW_REST_FRAC=0.25,
            _personality_active=None,
            motion_enabled=True,
            IDLE_LIFE_ENABLED=True,
            _day={"listen": None, "speak": None, "vol": None},
            _calibration_safe_outputs=mock.DEFAULT,
        ):
            runtime._handle_calibration_command({"action": "save"})

            self.assertEqual(runtime.JAW_REST_FRAC, 0.25)
            self.assertTrue(runtime._calibration_session.active)
            self.assertEqual(runtime._calibration_last_result, "error")
            self.assertIn("disk read-only", runtime._calibration_last_error)

    def test_unlock_cancels_session_before_clearing_interlock(self):
        session = session_with()
        controller = SkeletonController(lambda _event: None)
        controller.set_maintenance_active(True)
        with self.base_patch(
            _calibration_session=session,
            controller=controller,
            _cancel_motion_timer=mock.DEFAULT,
            _stop_eyes_effect=mock.DEFAULT,
            _jaw_set=mock.DEFAULT,
            eyes_off=mock.DEFAULT,
            eyes_idle=mock.DEFAULT,
            _publish_operator_controls=mock.DEFAULT,
            _publish_maintenance_state=mock.DEFAULT,
            _publish_idle_life_ready_state=mock.DEFAULT,
            _publish_scene_ready_state=mock.DEFAULT,
            _publish_self_test_state=mock.DEFAULT,
            _publish_barge_in_capability=mock.DEFAULT,
            _persist_operator_settings=mock.Mock(return_value=True),
            _calibration_safe_outputs=mock.DEFAULT,
        ):
            runtime._set_maintenance_mode(False)

            self.assertFalse(runtime._calibration_session.active)
            self.assertFalse(runtime.maintenance_mode)
            self.assertEqual(runtime._calibration_last_result, "cancelled")

    def test_other_control_is_rejected_during_active_session(self):
        message = SimpleNamespace(
            topic="holiday/skeleton/volume/set",
            payload=b"50",
        )
        with self.base_patch(
            _calibration_session=session_with(),
            _enqueue=mock.DEFAULT,
        ) as patched:
            runtime._on_message(None, None, message)

            patched["_enqueue"].assert_not_called()
            self.assertEqual(runtime._calibration_last_result, "busy")


if __name__ == "__main__":
    unittest.main()
