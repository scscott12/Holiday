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


if __name__ == "__main__":
    unittest.main()
