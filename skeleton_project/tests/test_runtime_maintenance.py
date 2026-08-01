import unittest
import threading
from types import SimpleNamespace
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.controller import Event, EventKind, RuntimeState, SkeletonController


class RuntimeMaintenanceTests(unittest.TestCase):
    def test_request_sets_immediate_interrupt_without_touching_hardware(self):
        controller = SkeletonController(lambda _event: None)
        published = []
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=False,
            _maintenance_state="ready",
            _maintenance_last_result="never",
            _maintenance_last_error="none",
            mqtt_pub=lambda topic, payload, retain=False: published.append(
                (topic, payload)
            ),
        ):
            self.assertTrue(runtime._request_maintenance_mode("ON"))

            self.assertTrue(controller.maintenance_interrupt_event.is_set())
            self.assertEqual(runtime._maintenance_state, "locking")
            self.assertIn(("maintenance/last_result", "queued"), published)

    def test_malformed_request_cannot_unlock_active_maintenance(self):
        controller = SkeletonController(lambda _event: None)
        controller.set_maintenance_active(True)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=True,
            mqtt_pub=mock.DEFAULT,
        ):
            self.assertFalse(runtime._request_maintenance_mode("maybe"))

            self.assertTrue(controller.maintenance_interrupt_event.is_set())
            self.assertTrue(runtime.maintenance_mode)
            self.assertEqual(runtime._maintenance_last_result, "error")

    def test_lock_transition_rests_outputs_and_persists(self):
        controller = SkeletonController(lambda _event: None)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=False,
            _maintenance_state="ready",
            _cancel_motion_timer=mock.DEFAULT,
            _stop_eyes_effect=mock.DEFAULT,
            _jaw_set=mock.DEFAULT,
            eyes_off=mock.DEFAULT,
            eyes_idle=mock.DEFAULT,
            _health_set=mock.DEFAULT,
            _publish_operator_controls=mock.DEFAULT,
            _publish_maintenance_state=mock.DEFAULT,
            _publish_idle_life_ready_state=mock.DEFAULT,
            _publish_scene_ready_state=mock.DEFAULT,
            _publish_self_test_state=mock.DEFAULT,
            _publish_barge_in_capability=mock.DEFAULT,
            _persist_operator_settings=mock.DEFAULT,
        ) as patched:
            runtime._set_maintenance_mode(True)

            self.assertTrue(runtime.maintenance_mode)
            self.assertTrue(controller.maintenance_interrupt_event.is_set())
            self.assertEqual(runtime._maintenance_state, "locked")
            patched["_cancel_motion_timer"].assert_called_once_with()
            patched["_jaw_set"].assert_called_with(runtime.JAW_REST_FRAC)
            patched["eyes_off"].assert_called_once_with()
            patched["_persist_operator_settings"].assert_called_once_with()

    def test_unlock_clears_interlock_and_restores_idle_outputs(self):
        controller = SkeletonController(lambda _event: None)
        controller.set_maintenance_active(True)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=True,
            _maintenance_state="locked",
            _cancel_motion_timer=mock.DEFAULT,
            _stop_eyes_effect=mock.DEFAULT,
            _jaw_set=mock.DEFAULT,
            eyes_off=mock.DEFAULT,
            eyes_idle=mock.DEFAULT,
            _health_set=mock.DEFAULT,
            _publish_operator_controls=mock.DEFAULT,
            _publish_maintenance_state=mock.DEFAULT,
            _publish_idle_life_ready_state=mock.DEFAULT,
            _publish_scene_ready_state=mock.DEFAULT,
            _publish_self_test_state=mock.DEFAULT,
            _publish_barge_in_capability=mock.DEFAULT,
            _persist_operator_settings=mock.DEFAULT,
        ) as patched:
            runtime._set_maintenance_mode(False)

            self.assertFalse(runtime.maintenance_mode)
            self.assertFalse(controller.maintenance_interrupt_event.is_set())
            self.assertEqual(runtime._maintenance_state, "ready")
            patched["eyes_idle"].assert_called_once_with()
            patched["_persist_operator_settings"].assert_called_once_with()

    def test_lock_remains_active_when_persistence_fails_and_reports_it(self):
        controller = SkeletonController(lambda _event: None)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=False,
            _settings_store=object(),
            _settings_last_error="disk is read-only",
            _cancel_motion_timer=mock.DEFAULT,
            _stop_eyes_effect=mock.DEFAULT,
            _jaw_set=mock.DEFAULT,
            eyes_off=mock.DEFAULT,
            eyes_idle=mock.DEFAULT,
            _health_set=mock.DEFAULT,
            _publish_operator_controls=mock.DEFAULT,
            _publish_maintenance_state=mock.DEFAULT,
            _publish_idle_life_ready_state=mock.DEFAULT,
            _publish_scene_ready_state=mock.DEFAULT,
            _publish_self_test_state=mock.DEFAULT,
            _publish_barge_in_capability=mock.DEFAULT,
            _persist_operator_settings=mock.Mock(return_value=False),
        ):
            runtime._set_maintenance_mode(True)

            self.assertTrue(runtime.maintenance_mode)
            self.assertTrue(controller.maintenance_interrupt_event.is_set())
            self.assertEqual(runtime._maintenance_last_result, "locked_unsaved")
            self.assertEqual(runtime._maintenance_last_error, "disk is read-only")

    def test_mqtt_speech_is_rejected_while_locked(self):
        controller = SkeletonController(lambda _event: None)
        controller.set_maintenance_active(True)
        message = SimpleNamespace(
            topic="holiday/skeleton/say/set",
            payload=b"move the jaw",
        )
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=True,
            _maintenance_rejected_count=0,
            _enqueue=mock.DEFAULT,
            mqtt_pub=mock.DEFAULT,
        ) as patched:
            runtime._on_message(None, None, message)

            patched["_enqueue"].assert_not_called()
            self.assertEqual(runtime._maintenance_rejected_count, 1)
            self.assertEqual(runtime._maintenance_last_result, "blocked")

    def test_confirmed_pir_motion_cannot_queue_a_visit_while_locked(self):
        with mock.patch.multiple(
            runtime,
            maintenance_mode=True,
            motion_enabled=True,
            pir=SimpleNamespace(motion_detected=True),
            _motion_timer=object(),
            _enqueue=mock.DEFAULT,
        ) as patched:
            runtime._confirm_motion()

            patched["_enqueue"].assert_not_called()
            self.assertIsNone(runtime._motion_timer)

    def test_prequeued_hardware_event_is_blocked_after_lock(self):
        controller = SkeletonController(lambda _event: None)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=True,
            _maintenance_reject=mock.DEFAULT,
            speak_with_jaw=mock.DEFAULT,
            _snooze_idle_life=mock.DEFAULT,
        ) as patched:
            runtime._handle_event(
                Event(EventKind.SAY, "queued before lock", "test")
            )

            patched["_maintenance_reject"].assert_called_once_with("say")
            patched["speak_with_jaw"].assert_not_called()
            self.assertEqual(controller.state, RuntimeState.MAINTENANCE)

    def test_hardware_writes_are_clamped_safe_during_pending_lock(self):
        controller = SkeletonController(lambda _event: None)
        controller.request_maintenance(True, "test")
        jaw = SimpleNamespace(fraction=None)
        eyes = SimpleNamespace(duty_cycle=None)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=False,
            _jaw=jaw,
            _eyes_ch=eyes,
            EYES_INVERT=0,
            JAW_REST_FRAC=0.25,
        ):
            self.assertTrue(runtime._jaw_set(0.9))
            self.assertTrue(runtime.eyes_set(0.8))

            self.assertEqual(jaw.fraction, 0.25)
            self.assertEqual(eyes.duty_cycle, 0)

    def test_pending_lock_prevents_legacy_piper_from_starting(self):
        controller = SkeletonController(lambda _event: None)
        controller.request_maintenance(True, "test")
        with mock.patch.multiple(
            runtime,
            controller=controller,
            maintenance_mode=False,
            subprocess=mock.DEFAULT,
        ) as patched:
            runtime._legacy_speak_with_jaw(
                "do not play",
                controller.maintenance_interrupt_event,
            )

            patched["subprocess"].Popen.assert_not_called()

    def test_active_legacy_piper_is_terminated_when_lock_arrives(self):
        stop = threading.Event()

        class Input:
            def write(self, _text):
                return None

            def close(self):
                return None

        class Process:
            def __init__(self):
                self.stdin = Input()
                self.returncode = None
                self.terminated = False

            def poll(self):
                stop.set()
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                self.returncode = -15
                return self.returncode

            def kill(self):
                self.returncode = -9

        process = Process()
        with mock.patch.object(runtime.subprocess, "Popen", return_value=process):
            runtime._legacy_speak_with_jaw("stop safely", stop)

        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
