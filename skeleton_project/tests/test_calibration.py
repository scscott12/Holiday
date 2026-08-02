import unittest

from holiday_skeleton.calibration import (
    CALIBRATION_STEPS,
    CalibrationConfigError,
    CalibrationSession,
    CalibrationStep,
    CalibrationValues,
    HardwareCalibration,
    hardware_calibration_from_payload,
)


def values(**overrides):
    configured = {
        "hardware": HardwareCalibration(),
        "eyes_dim": 0.18,
        "eyes_full": 1.0,
        "speaker_volume": 1.0,
    }
    configured.update(overrides)
    return CalibrationValues(**configured)


class HardwareCalibrationTests(unittest.TestCase):
    def test_round_trip_preserves_all_physical_values(self):
        configured = HardwareCalibration(
            jaw_rest=0.2,
            jaw_max=0.85,
            eyes_inverted=True,
            microphone_gate=425,
            pir_hold_seconds=1.2,
            pir_cooldown_seconds=14,
        )

        restored = hardware_calibration_from_payload(configured.to_payload())

        self.assertEqual(restored, configured)

    def test_rejects_unknown_missing_non_finite_and_unsafe_values(self):
        payload = HardwareCalibration().to_payload()
        payload["secret"] = "no"
        with self.assertRaisesRegex(CalibrationConfigError, "unknown fields"):
            hardware_calibration_from_payload(payload)

        payload = HardwareCalibration().to_payload()
        payload.pop("jaw_rest")
        with self.assertRaisesRegex(CalibrationConfigError, "missing fields"):
            hardware_calibration_from_payload(payload)

        with self.assertRaisesRegex(CalibrationConfigError, "microphone_gate"):
            HardwareCalibration(microphone_gate=float("nan")).validated()

        with self.assertRaisesRegex(CalibrationConfigError, "at least 0.05"):
            HardwareCalibration(jaw_rest=0.5, jaw_max=0.52).validated()

    def test_complete_values_reject_inverted_eye_levels(self):
        with self.assertRaisesRegex(CalibrationConfigError, "eyes_full"):
            values(eyes_dim=0.8, eyes_full=0.4).validated()


class CalibrationSessionTests(unittest.TestCase):
    def test_stages_changes_without_mutating_original(self):
        original = values()
        session = CalibrationSession()
        session.start(original)

        staged = session.update(CalibrationStep.JAW_REST, 0.3)
        session.update(CalibrationStep.SPEAKER_VOLUME, 1.25)

        self.assertEqual(original.hardware.jaw_rest, 0.25)
        self.assertEqual(staged.hardware.jaw_rest, 0.3)
        self.assertEqual(session.staged.speaker_volume, 1.25)

    def test_step_selection_and_next_follow_guided_order(self):
        session = CalibrationSession()
        session.start(values())

        self.assertEqual(session.step, CALIBRATION_STEPS[0])
        session.select(CalibrationStep.MICROPHONE_GATE)
        self.assertIn("ambient noise", session.instruction)
        self.assertEqual(session.next(), CalibrationStep.SPEAKER_VOLUME)

    def test_staging_allows_pair_to_be_fixed_before_final_validation(self):
        session = CalibrationSession()
        session.start(values())
        session.update(CalibrationStep.JAW_MAX, 0.3)
        session.update(CalibrationStep.JAW_REST, 0.29)

        with self.assertRaisesRegex(CalibrationConfigError, "jaw_max"):
            session.validated()

        session.update(CalibrationStep.JAW_MAX, 0.9)
        self.assertEqual(session.validated().hardware.jaw_max, 0.9)

    def test_complete_clears_session_and_returns_validated_values(self):
        session = CalibrationSession()
        session.start(values())
        session.update(CalibrationStep.PIR_HOLD, 1.5)

        completed = session.complete()

        self.assertFalse(session.active)
        self.assertEqual(completed.hardware.pir_hold_seconds, 1.5)
        with self.assertRaisesRegex(CalibrationConfigError, "no calibration"):
            _ = session.staged

    def test_cancel_returns_original_and_discards_staged_values(self):
        original = values()
        session = CalibrationSession()
        session.start(original)
        session.update(CalibrationStep.EYES_FULL, 0.7)

        restored = session.cancel()

        self.assertEqual(restored, original)
        self.assertFalse(session.active)

    def test_commands_require_an_active_session_and_known_step(self):
        session = CalibrationSession()
        with self.assertRaisesRegex(CalibrationConfigError, "start calibration"):
            session.next()

        session.start(values())
        with self.assertRaisesRegex(CalibrationConfigError, "unknown calibration step"):
            session.select("not_a_step")


if __name__ == "__main__":
    unittest.main()
