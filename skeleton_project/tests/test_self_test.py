import json
import threading
import unittest

from holiday_skeleton.self_test import (
    SelfTestInterrupted,
    SelfTestRunner,
    SelfTestStep,
    SelfTestStepSkipped,
    SelfTestStepStatus,
)


class ManualClock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value


class AdvancingEvent:
    def __init__(self, clock):
        self.clock = clock
        self.set_value = False

    def is_set(self):
        return self.set_value

    def set(self):
        self.set_value = True

    def wait(self, timeout=None):
        self.clock.value += float(timeout or 0.0)
        return self.set_value


class SelfTestRunnerTests(unittest.TestCase):
    def test_all_steps_pass_in_order_and_publish_progress(self):
        called = []
        progress = []
        runner = SelfTestRunner(
            progress=lambda index, total, step: progress.append(
                (index, total, step.name)
            )
        )
        steps = (
            SelfTestStep("eyes", lambda _stop: called.append("eyes") or "two pulses"),
            SelfTestStep("jaw", lambda _stop: called.append("jaw") or "two moves"),
        )

        result = runner.run(steps, threading.Event())

        self.assertEqual(result.outcome, "passed")
        self.assertEqual(called, ["eyes", "jaw"])
        self.assertEqual(progress, [(1, 2, "eyes"), (2, 2, "jaw")])
        self.assertTrue(all(step.status is SelfTestStepStatus.PASSED for step in result.steps))

    def test_skipped_step_degrades_but_later_steps_continue(self):
        def unavailable(_stop):
            raise SelfTestStepSkipped("speaker unavailable")

        result = SelfTestRunner().run(
            (
                SelfTestStep("speaker", unavailable),
                SelfTestStep("jaw", lambda _stop: "moved"),
            ),
            threading.Event(),
        )

        self.assertEqual(result.outcome, "degraded")
        self.assertEqual(
            [step.status for step in result.steps],
            [SelfTestStepStatus.SKIPPED, SelfTestStepStatus.PASSED],
        )

    def test_failed_step_is_reported_and_later_steps_continue(self):
        def fail(_stop):
            raise RuntimeError("PWM write failed")

        result = SelfTestRunner().run(
            (
                SelfTestStep("eyes", fail),
                SelfTestStep("jaw", lambda _stop: "moved"),
            ),
            threading.Event(),
        )

        self.assertEqual(result.outcome, "failed")
        self.assertEqual(result.error, "eyes: PWM write failed")
        self.assertEqual(len(result.steps), 2)

    def test_foreground_interrupt_stops_before_next_step(self):
        interrupt = threading.Event()

        def interrupted(_stop):
            interrupt.set()
            raise SelfTestInterrupted()

        result = SelfTestRunner().run(
            (
                SelfTestStep("eyes", interrupted),
                SelfTestStep("jaw", lambda _stop: self.fail("jaw should not run")),
            ),
            interrupt,
        )

        self.assertEqual(result.outcome, "interrupted")
        self.assertTrue(result.interrupted)
        self.assertEqual(result.steps, ())

    def test_deadline_turns_a_cooperative_wait_into_timeout(self):
        clock = ManualClock()
        event = AdvancingEvent(clock)

        def wait_too_long(stop):
            stop.wait(10.0)
            return "late"

        result = SelfTestRunner(maximum_seconds=1.0, clock=clock).run(
            (SelfTestStep("speaker", wait_too_long),),
            event,
        )

        self.assertEqual(result.outcome, "timed_out")
        self.assertTrue(result.interrupted)
        self.assertTrue(result.timed_out)
        self.assertEqual(result.duration_seconds, 1.0)

    def test_report_json_contains_bounded_structured_results(self):
        result = SelfTestRunner().run(
            (SelfTestStep("eyes", lambda _stop: "ok"),),
            threading.Event(),
        )

        payload = json.loads(result.report_json())

        self.assertEqual(payload["outcome"], "passed")
        self.assertEqual(payload["steps"][0]["name"], "eyes")
        self.assertEqual(payload["steps"][0]["status"], "passed")


if __name__ == "__main__":
    unittest.main()
