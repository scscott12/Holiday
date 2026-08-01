import threading
import unittest
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.controller import RuntimeState
from holiday_skeleton.health import ComponentState
from holiday_skeleton.self_test import (
    SelfTestResult,
    SelfTestStepResult,
    SelfTestStepSkipped,
    SelfTestStepStatus,
)


class RuntimeSelfTestTests(unittest.TestCase):
    class NoWaitSignal:
        def wait(self, _timeout=None):
            return False

        def is_set(self):
            return False

    def test_busy_runtime_rejects_request_without_interrupting_work(self):
        controller = mock.Mock()
        controller.state = RuntimeState.SPEAKING
        with mock.patch.multiple(
            runtime,
            SELF_TEST_ENABLED=True,
            _self_test_runner=object(),
            _self_test_active=False,
            _self_test_pending=False,
            controller=controller,
            mqtt_pub=mock.DEFAULT,
        ):
            accepted = runtime._request_self_test()

        self.assertFalse(accepted)
        controller.enqueue.assert_not_called()

    def test_successful_run_publishes_report_and_restores_outputs(self):
        result = SelfTestResult(
            outcome="passed",
            steps=(
                SelfTestStepResult("eyes", SelfTestStepStatus.PASSED, "two pulses"),
                SelfTestStepResult("jaw", SelfTestStepStatus.PASSED, "two moves"),
            ),
            duration_seconds=1.25,
        )
        runner = mock.Mock()
        runner.run.return_value = result
        controller = mock.Mock()
        controller.self_test_interrupt_event = threading.Event()
        published = []
        with mock.patch.multiple(
            runtime,
            _self_test_runner=runner,
            _self_test_active=False,
            _self_test_pending=False,
            _self_test_cancel_pending=False,
            _self_test_step="none",
            _self_test_last_result="never",
            _self_test_last_error="none",
            _self_test_last_run="never",
            _self_test_count=0,
            _self_test_interrupted=0,
            _self_test_report="{}",
            controller=controller,
            mqtt_pub=lambda topic, payload, retain=False: published.append((topic, payload)),
            _health_set=mock.DEFAULT,
            _jaw_set=mock.DEFAULT,
            eyes_idle=mock.DEFAULT,
        ) as patched:
            runtime._run_self_test()

            self.assertEqual(runtime._self_test_last_result, "passed")
            self.assertEqual(runtime._self_test_count, 1)
            self.assertIn(('self_test/last_result', 'passed'), published)
            self.assertIn(('self_test/active', 'OFF'), published)
            patched["_health_set"].assert_called_with(
                "self_test", ComponentState.READY, "last run passed"
            )
            patched["_jaw_set"].assert_called_with(runtime.JAW_REST_FRAC)
            patched["eyes_idle"].assert_called_once()
        controller.set_state.assert_called_with(RuntimeState.SELF_TEST)

    def test_streaming_speaker_is_skipped_when_only_legacy_tts_exists(self):
        with mock.patch.object(runtime, "_speech_engine", None):
            with self.assertRaises(SelfTestStepSkipped):
                runtime._self_test_speaker(threading.Event())

    def test_speaker_step_requires_confirmed_streaming_completion(self):
        with mock.patch.multiple(
            runtime,
            _speech_engine=object(),
            speak_with_jaw=mock.DEFAULT,
        ) as patched:
            patched["speak_with_jaw"].return_value = None
            with self.assertRaisesRegex(RuntimeError, "did not complete"):
                runtime._self_test_speaker(threading.Event())

    def test_speaker_step_accepts_completed_streaming_playback(self):
        def complete(_text, streaming_result=None, **_kwargs):
            streaming_result(mock.Mock(interrupted=False))
            return None

        with mock.patch.multiple(
            runtime,
            _speech_engine=object(),
            speak_with_jaw=complete,
        ):
            detail = runtime._self_test_speaker(threading.Event())

        self.assertEqual(detail, "streaming Piper audio played")

    def test_eye_and_jaw_commands_are_capped_at_conservative_travel(self):
        class EyeChannel:
            def __init__(self):
                self.writes = []

            @property
            def duty_cycle(self):
                return self.writes[-1] if self.writes else 0

            @duty_cycle.setter
            def duty_cycle(self, value):
                self.writes.append(value)

        class Jaw:
            def __init__(self):
                self.writes = []

            @property
            def fraction(self):
                return self.writes[-1] if self.writes else 0.0

            @fraction.setter
            def fraction(self, value):
                self.writes.append(value)

        eyes = EyeChannel()
        jaw = Jaw()
        with mock.patch.multiple(
            runtime,
            _eyes_ch=eyes,
            _jaw=jaw,
            EYES_INVERT=0,
            SELF_TEST_EYES_FRAC=1.0,
            SELF_TEST_JAW_FRAC=1.0,
            SELF_TEST_STEP_SEC=0.0,
            JAW_REST_FRAC=0.25,
            JAW_MAX_FRAC=1.0,
            night_mode=False,
        ):
            runtime._self_test_eyes(self.NoWaitSignal())
            runtime._self_test_jaw(self.NoWaitSignal())

        self.assertLessEqual(max(eyes.writes), int(0xFFFF * 0.35))
        self.assertLessEqual(max(jaw.writes), 0.25 + 0.75 * 0.35)

    def test_other_mqtt_command_interrupts_active_self_test(self):
        controller = mock.Mock()
        message = mock.Mock(
            topic="holiday/skeleton/personality/set",
            payload=b"graveyard_host",
        )
        with mock.patch.multiple(
            runtime,
            controller=controller,
            _self_test_active=True,
            _request_personality_switch=mock.DEFAULT,
        ):
            runtime._on_message(None, None, message)

        controller.interrupt_self_test.assert_called_once()

    def test_stop_button_only_interrupts_an_active_self_test(self):
        controller = mock.Mock()
        with mock.patch.multiple(
            runtime,
            controller=controller,
            _self_test_active=False,
            _self_test_pending=False,
        ):
            self.assertFalse(runtime._stop_self_test())
            controller.interrupt_self_test.assert_not_called()

        with mock.patch.multiple(
            runtime,
            controller=controller,
            _self_test_active=True,
            _self_test_pending=False,
            mqtt_pub=mock.DEFAULT,
        ):
            self.assertTrue(runtime._stop_self_test())
            controller.interrupt_self_test.assert_called_once()

    def test_stop_button_cancels_a_queued_self_test_before_hardware_moves(self):
        runner = mock.Mock()
        controller = mock.Mock()
        controller.state = RuntimeState.IDLE
        controller.enqueue.return_value = True
        with mock.patch.multiple(
            runtime,
            SELF_TEST_ENABLED=True,
            _self_test_runner=runner,
            _self_test_active=False,
            _self_test_pending=False,
            _self_test_cancel_pending=False,
            _self_test_count=0,
            _self_test_interrupted=0,
            _self_test_last_result="never",
            _self_test_last_error="none",
            _self_test_last_run="never",
            _self_test_report="{}",
            controller=controller,
            mqtt_pub=mock.DEFAULT,
            _health_set=mock.DEFAULT,
        ):
            self.assertTrue(runtime._request_self_test())
            self.assertTrue(runtime._stop_self_test())
            runtime._run_self_test()

            runner.run.assert_not_called()
            self.assertEqual(runtime._self_test_last_result, "interrupted")
            self.assertEqual(runtime._self_test_count, 1)
            self.assertEqual(runtime._self_test_interrupted, 1)


if __name__ == "__main__":
    unittest.main()
