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


if __name__ == "__main__":
    unittest.main()

