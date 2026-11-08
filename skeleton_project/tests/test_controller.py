import threading
import time
import unittest

from holiday_skeleton.controller import EventKind, RuntimeState, SkeletonController


class SkeletonControllerTests(unittest.TestCase):
    def test_events_are_serialized_and_keep_fifo_order(self):
        handled = []
        active = 0
        max_active = 0
        lock = threading.Lock()
        controller = None

        def handler(event):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.01)
            handled.append(event.payload)
            with lock:
                active -= 1
            if event.payload == 3:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        for value in (1, 2, 3):
            self.assertTrue(controller.enqueue(EventKind.SAY, value, "test"))

        controller.run_forever()

        self.assertEqual(handled, [1, 2, 3])
        self.assertEqual(max_active, 1)
        self.assertEqual(controller.state, RuntimeState.STOPPING)

    def test_trigger_is_coalesced_while_active(self):
        started = threading.Event()
        release = threading.Event()

        def handler(event):
            started.set()
            release.wait(timeout=1)

        controller = SkeletonController(handler)
        self.assertTrue(controller.enqueue(EventKind.TRIGGER, source="pir"))
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(started.wait(timeout=1))

        self.assertFalse(controller.enqueue(EventKind.TRIGGER, source="mqtt"))
        release.set()
        controller.request_stop("test")
        worker.join(timeout=1)

        self.assertFalse(worker.is_alive())

    def test_foreground_event_interrupts_idle_handler_before_running(self):
        idle_started = threading.Event()
        idle_finished = threading.Event()
        foreground_handled = threading.Event()
        interrupted = []
        controller = None

        def idle_handler(interrupt_event):
            idle_started.set()
            interrupted.append(interrupt_event.wait(timeout=1.0))
            idle_finished.set()

        def handler(event):
            foreground_handled.set()
            controller.request_stop("test")

        controller = SkeletonController(handler, idle_handler=idle_handler)
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(idle_started.wait(timeout=1.0))

        self.assertTrue(controller.enqueue(EventKind.SAY, "visitor", "test"))

        self.assertTrue(idle_finished.wait(timeout=1.0))
        self.assertTrue(foreground_handled.wait(timeout=1.0))
        worker.join(timeout=1.0)
        self.assertEqual(interrupted, [True])
        self.assertFalse(worker.is_alive())

    def test_foreground_event_interrupts_active_scene_before_running(self):
        scene_started = threading.Event()
        scene_finished = threading.Event()
        handled = []
        interrupted = []
        controller = None

        def handler(event):
            handled.append(event.kind)
            if event.kind is EventKind.PLAY_SCENE:
                scene_started.set()
                interrupted.append(
                    controller.scene_interrupt_event.wait(timeout=1.0)
                )
                scene_finished.set()
            else:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        self.assertTrue(controller.enqueue(EventKind.PLAY_SCENE, "awakening", "test"))
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(scene_started.wait(timeout=1.0))

        self.assertTrue(controller.enqueue(EventKind.SAY, "visitor", "test"))

        self.assertTrue(scene_finished.wait(timeout=1.0))
        worker.join(timeout=1.0)
        self.assertEqual(interrupted, [True])
        self.assertEqual(handled, [EventKind.PLAY_SCENE, EventKind.SAY])
        self.assertFalse(worker.is_alive())

    def test_scene_yields_when_a_later_event_is_already_queued(self):
        observed = []
        controller = None

        def handler(event):
            if event.kind is EventKind.PLAY_SCENE:
                observed.append(controller.scene_interrupt_event.is_set())
            else:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        controller.enqueue(EventKind.PLAY_SCENE, "awakening", "test")
        controller.enqueue(EventKind.SAY, "visitor", "test")

        controller.run_forever()

        self.assertEqual(observed, [True])

    def test_foreground_event_interrupts_active_self_test_before_running(self):
        test_started = threading.Event()
        test_finished = threading.Event()
        handled = []
        interrupted = []
        controller = None

        def handler(event):
            handled.append(event.kind)
            if event.kind is EventKind.RUN_SELF_TEST:
                test_started.set()
                interrupted.append(
                    controller.self_test_interrupt_event.wait(timeout=1.0)
                )
                test_finished.set()
            else:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        self.assertTrue(controller.enqueue(EventKind.RUN_SELF_TEST, source="test"))
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(test_started.wait(timeout=1.0))

        self.assertTrue(controller.enqueue(EventKind.SAY, "visitor", "test"))

        self.assertTrue(test_finished.wait(timeout=1.0))
        worker.join(timeout=1.0)
        self.assertEqual(interrupted, [True])
        self.assertEqual(handled, [EventKind.RUN_SELF_TEST, EventKind.SAY])
        self.assertFalse(worker.is_alive())

    def test_foreground_event_interrupts_content_reload_before_running(self):
        reload_started = threading.Event()
        reload_finished = threading.Event()
        handled = []
        interrupted = []
        controller = None

        def handler(event):
            handled.append(event.kind)
            if event.kind is EventKind.RELOAD_CONTENT:
                reload_started.set()
                interrupted.append(
                    controller.content_reload_interrupt_event.wait(timeout=1.0)
                )
                reload_finished.set()
            else:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        self.assertTrue(controller.enqueue(EventKind.RELOAD_CONTENT, source="test"))
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(reload_started.wait(timeout=1.0))

        self.assertTrue(controller.enqueue(EventKind.SAY, "visitor", "test"))

        self.assertTrue(reload_finished.wait(timeout=1.0))
        worker.join(timeout=1.0)
        self.assertEqual(interrupted, [True])
        self.assertEqual(handled, [EventKind.RELOAD_CONTENT, EventKind.SAY])
        self.assertFalse(worker.is_alive())

    def test_content_reload_yields_when_a_later_event_is_already_queued(self):
        observed = []
        controller = None

        def handler(event):
            if event.kind is EventKind.RELOAD_CONTENT:
                observed.append(controller.content_reload_interrupt_event.is_set())
            else:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        controller.enqueue(EventKind.RELOAD_CONTENT, source="test")
        controller.enqueue(EventKind.SAY, "visitor", "test")

        controller.run_forever()

        self.assertEqual(observed, [True])

    def test_self_test_yields_when_a_later_event_is_already_queued(self):
        observed = []
        controller = None

        def handler(event):
            if event.kind is EventKind.RUN_SELF_TEST:
                observed.append(controller.self_test_interrupt_event.is_set())
            else:
                controller.request_stop("test")

        controller = SkeletonController(handler)
        controller.enqueue(EventKind.RUN_SELF_TEST, source="test")
        controller.enqueue(EventKind.SAY, "visitor", "test")

        controller.run_forever()

        self.assertEqual(observed, [True])

    def test_controller_heartbeat_reports_state_and_survives_callback_failure(self):
        observed = []
        controller = None

        def heartbeat(state):
            observed.append(state)
            if len(observed) == 2:
                raise RuntimeError("watchdog reporting must be isolated")

        def handler(_event):
            controller.request_stop("test")

        controller = SkeletonController(handler, heartbeat=heartbeat)
        controller.enqueue(EventKind.SAY, "hello", "test")

        controller.run_forever()

        self.assertIn(RuntimeState.IDLE, observed)
        self.assertEqual(controller.state, RuntimeState.STOPPING)

    def test_maintenance_request_interrupts_and_preempts_queued_work(self):
        scene_started = threading.Event()
        handled = []
        interrupted = []
        controller = None

        def handler(event):
            handled.append(event.kind)
            if event.kind is EventKind.PLAY_SCENE:
                scene_started.set()
                interrupted.append(
                    controller.maintenance_interrupt_event.wait(timeout=1.0)
                )
            elif event.kind is EventKind.SET_MAINTENANCE_MODE:
                controller.set_maintenance_active(event.payload)
                controller.request_stop("test")

        controller = SkeletonController(handler)
        controller.enqueue(EventKind.PLAY_SCENE, "awakening", "test")
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(scene_started.wait(timeout=1.0))
        self.assertTrue(controller.enqueue(EventKind.SAY, "queued", "test"))

        self.assertTrue(controller.request_maintenance(True, "test"))

        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())
        self.assertEqual(interrupted, [True])
        self.assertEqual(
            handled,
            [EventKind.PLAY_SCENE, EventKind.SET_MAINTENANCE_MODE],
        )

    def test_initial_maintenance_state_does_not_run_idle_handler(self):
        idle_calls = []
        controller = None

        def handler(event):
            self.assertEqual(event.kind, EventKind.SET_MAINTENANCE_MODE)
            controller.request_stop("test")

        controller = SkeletonController(
            handler,
            idle_handler=lambda _interrupt: idle_calls.append(True),
            initial_state=RuntimeState.MAINTENANCE,
        )
        controller.request_maintenance(True, "test")

        controller.run_forever()

        self.assertEqual(idle_calls, [])


    def test_full_queue_rejects_events_and_releases_trigger_slot(self):
        controller = SkeletonController(lambda event: None, max_queue_size=1)

        self.assertTrue(controller.enqueue(EventKind.SAY, "a"))
        self.assertFalse(controller.enqueue(EventKind.TRIGGER))
        self.assertFalse(controller.enqueue(EventKind.SAY, "b"))

        controller._queue.get_nowait()
        self.assertTrue(controller.enqueue(EventKind.TRIGGER))

    def test_enqueue_after_stop_only_accepts_shutdown(self):
        controller = SkeletonController(lambda event: None)
        controller.request_stop("test")

        self.assertFalse(controller.enqueue(EventKind.SAY, "late"))
        self.assertFalse(controller.enqueue(EventKind.TRIGGER))
        self.assertTrue(controller.enqueue(EventKind.SHUTDOWN))
        self.assertFalse(controller.request_maintenance(True))

    def test_request_stop_with_full_queue_still_stops_loop(self):
        handled = []
        controller = SkeletonController(handled.append, max_queue_size=1)
        controller.enqueue(EventKind.SAY, "x")
        controller.request_stop("test")

        controller.run_forever()

        self.assertTrue(controller.stop_event.is_set())
        self.assertEqual(controller.state, RuntimeState.STOPPING)
        self.assertTrue(controller.maintenance_interrupt_event.is_set())

    def test_handler_failure_sets_error_state_and_clears_trigger(self):
        states = []

        def handler(event):
            raise RuntimeError("boom")

        controller = SkeletonController(handler, state_changed=states.append)
        controller.enqueue(EventKind.TRIGGER)

        with self.assertRaises(RuntimeError):
            controller.run_forever()

        self.assertEqual(controller.state, RuntimeState.ERROR)
        self.assertEqual(states[-1], RuntimeState.ERROR)
        self.assertTrue(controller.enqueue(EventKind.TRIGGER))

    def test_idle_handler_failure_sets_error_state(self):
        def idle(interrupt):
            raise ValueError("idle broke")

        controller = SkeletonController(lambda event: None, idle_handler=idle)

        with self.assertRaises(ValueError):
            controller.run_forever()

        self.assertEqual(controller.state, RuntimeState.ERROR)

    def test_state_changed_only_fires_on_actual_change(self):
        states = []
        controller = SkeletonController(
            lambda event: None, state_changed=states.append
        )

        controller.set_state(RuntimeState.IDLE)
        controller.set_state(RuntimeState.IDLE)
        controller.set_state(RuntimeState.ERROR)

        self.assertEqual(states, [RuntimeState.IDLE, RuntimeState.ERROR])

    def test_latest_maintenance_request_wins_and_is_consumed_once(self):
        controller = SkeletonController(lambda event: None)

        self.assertTrue(controller.request_maintenance(True, "a"))
        self.assertTrue(controller.request_maintenance(False, "b"))
        event = controller._take_maintenance_request()

        self.assertEqual(event.kind, EventKind.SET_MAINTENANCE_MODE)
        self.assertIs(event.payload, False)
        self.assertEqual(event.source, "b")
        self.assertIsNone(controller._take_maintenance_request())

    def test_set_maintenance_active_toggles_interrupt(self):
        controller = SkeletonController(lambda event: None)

        controller.set_maintenance_active(True)
        self.assertTrue(controller.maintenance_interrupt_event.is_set())
        controller.set_maintenance_active(False)
        self.assertFalse(controller.maintenance_interrupt_event.is_set())

    def test_concurrent_triggers_are_coalesced_to_one(self):
        controller = SkeletonController(lambda event: None)
        results = []
        barrier = threading.Barrier(8)

        def push():
            barrier.wait(timeout=1)
            results.append(controller.enqueue(EventKind.TRIGGER))

        threads = [threading.Thread(target=push) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=1)

        self.assertEqual(results.count(True), 1)
        self.assertEqual(controller._queue.qsize(), 1)

    def test_heartbeat_failure_does_not_block_events(self):
        handled = []
        controller = None

        def handler(event):
            handled.append(event.payload)
            controller.request_stop("test")

        def bad_heartbeat(state):
            raise OSError("watchdog")

        controller = SkeletonController(handler, heartbeat=bad_heartbeat)
        controller.enqueue(EventKind.SAY, 1)
        controller.run_forever()

        self.assertEqual(handled, [1])


class ControllerIntegrationTests(unittest.TestCase):
    """The controller is the seam between the runtime's event sources and the
    modules that own hardware, health, and watchdog side effects.  These tests
    assert that seam works: events reach the handler, and state/heartbeat/
    interrupt signals reach the other modules without blocking the runtime."""

    def _integration_controller(self, **kwargs):
        # The runtime wires the controller exactly this way in main():
        # SkeletonController(_handle_event, _runtime_state_changed,
        #                     idle_handler=_idle_tick,
        #                     heartbeat=_controller_heartbeat, ...).
        return SkeletonController(
            self._handler,
            self._state_changed,
            heartbeat=self._heartbeat,
            **kwargs,
        )

    def _make_runtime(self):
        self._handled = []
        self._state_changes = []
        self._heartbeats = []

        def handler(event):
            self._handled.append(event)
            if event.payload == "stop":
                self._controller.request_stop("integration")

        def state_changed(state):
            self._state_changes.append(state)

        def heartbeat(state):
            self._heartbeats.append(state)

        self._handler = handler
        self._state_changed = state_changed
        self._heartbeat = heartbeat
        controller = SkeletonController(
            handler,
            state_changed,
            heartbeat=heartbeat,
        )
        self._controller = controller
        return controller

    def test_runtime_wires_all_integration_callbacks(self):
        controller = self._make_runtime()
        controller.enqueue(EventKind.SAY, "hello", "test")
        controller.enqueue(EventKind.SAY, "stop", "test")

        controller.run_forever()

        # A foreground event reached the runtime handler (enqueue integration).
        self.assertTrue(
            any(
                e.kind is EventKind.SAY and e.payload == "hello"
                for e in self._handled
            )
        )
        # The runtime's state_changed callback saw the lifecycle transitions
        # the controller owns (state_changed integration).
        self.assertIn(RuntimeState.IDLE, self._state_changes)
        self.assertIn(RuntimeState.STOPPING, self._state_changes)
        # The watchdog heartbeat received progress across the loop
        # (heartbeat integration); a failure in the callback must not stop it.
        self.assertGreaterEqual(len(self._heartbeats), 2)
        self.assertEqual(controller.state, RuntimeState.STOPPING)

    def test_state_changed_notifies_other_modules_only_on_change(self):
        controller = self._make_runtime()
        controller.set_state(RuntimeState.IDLE)
        controller.set_state(RuntimeState.IDLE)  # no change
        controller.set_state(RuntimeState.SPEAKING)

        # The health/MQTT status module must observe each real transition once.
        self.assertEqual(self._state_changes, [RuntimeState.IDLE, RuntimeState.SPEAKING])
        self.assertEqual(controller.state, RuntimeState.SPEAKING)

    def test_heartbeat_reaches_watchdog_without_blocking_work(self):
        # A watchdog callback failure must not stop the controller from running
        # other modules' work; it only reports progress.
        def bad_heartbeat(_state):
            raise RuntimeError("watchdog reporting must be isolated")

        handled = []

        def handler(event):
            handled.append(event.payload)
            controller.request_stop("test")

        controller = SkeletonController(handler, heartbeat=bad_heartbeat)
        controller.enqueue(EventKind.SAY, "visitor", "test")
        controller.run_forever()

        self.assertEqual(handled, ["visitor"])
        self.assertEqual(controller.state, RuntimeState.STOPPING)

    def test_maintenance_interrupt_reaches_other_modules(self):
        # A maintenance request from the runtime must raise the shared interrupt
        # that idle/scene/self-test/content-reload modules watch, then be
        # consumed once as a serialized event ahead of ordinary queued work.
        observed = []
        controller = None

        def handler(event):
            observed.append(event.kind)
            if event.kind is EventKind.SET_MAINTENANCE_MODE:
                controller.set_maintenance_active(event.payload)
                controller.request_stop("test")

        controller = SkeletonController(handler)
        self.assertTrue(controller.request_maintenance(True, "mqtt"))

        # The shared interrupt is set immediately, before the event is consumed.
        self.assertTrue(controller.maintenance_interrupt_event.is_set())
        controller.run_forever()

        self.assertEqual(observed, [EventKind.SET_MAINTENANCE_MODE])
        # The runtime handler sets the lockout active; the runtime keeps the
        # shared interrupt raised for the whole lockout, so it stays set.
        self.assertTrue(controller.maintenance_interrupt_event.is_set())

    def test_foreground_event_interrupts_other_modules_before_running(self):
        # A foreground event must wake an in-progress module (idle life) before
        # the controller runs its own handler, so the module yields safely.
        idle_started = threading.Event()
        idle_finished = threading.Event()
        foreground_handled = threading.Event()
        controller = None

        def idle_handler(interrupt_event):
            idle_started.set()
            interrupt_event.wait(timeout=1.0)
            idle_finished.set()

        def handler(event):
            foreground_handled.set()
            controller.request_stop("test")

        controller = SkeletonController(handler, idle_handler=idle_handler)
        worker = threading.Thread(target=controller.run_forever)
        worker.start()
        self.assertTrue(idle_started.wait(timeout=1.0))

        self.assertTrue(controller.enqueue(EventKind.SAY, "visitor", "mqtt"))
        self.assertTrue(idle_finished.wait(timeout=1.0))
        self.assertTrue(foreground_handled.wait(timeout=1.0))
        worker.join(timeout=1.0)
        self.assertFalse(worker.is_alive())


if __name__ == "__main__":
    unittest.main()
