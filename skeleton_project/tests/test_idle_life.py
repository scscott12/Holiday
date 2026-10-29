import random
import unittest

from holiday_skeleton.idle_life import IdleAction, IdleLifeScheduler


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class FixedRandom:
    def __init__(self, chance=0.0):
        self.chance = chance

    def uniform(self, minimum, maximum):
        return minimum

    def random(self):
        return self.chance

    def choice(self, values):
        return tuple(values)[0]


def make_scheduler(minimum=10, maximum=10, chance=0.0, lines=(), enabled=True):
    """A scheduler with a deterministic clock and a seeded RNG."""

    clock = FakeClock()
    rng = random.Random(1234)
    scheduler = IdleLifeScheduler(
        minimum_interval=minimum,
        maximum_interval=maximum,
        mutter_chance=0.0 if not lines else 1.0,
        mutter_lines=lines,
        enabled=enabled,
        rng=rng,
        clock=clock,
    )
    return clock, scheduler


class IdleLifeSchedulerTests(unittest.TestCase):
    def test_armed_scheduler_waits_then_selects_a_mutter(self):
        clock = FakeClock()
        scheduler = IdleLifeScheduler(
            minimum_interval=10,
            maximum_interval=10,
            mutter_chance=1.0,
            mutter_lines=("Still here.",),
            rng=FixedRandom(chance=0.0),
            clock=clock,
        )

        self.assertIsNone(scheduler.poll(armed=True))
        self.assertEqual(scheduler.next_due, 10.0)
        clock.now = 9.9
        self.assertIsNone(scheduler.poll(armed=True))
        clock.now = 10.0

        decision = scheduler.poll(armed=True)

        self.assertEqual(decision.action, IdleAction.MUTTER)
        self.assertEqual(decision.text, "Still here.")
        self.assertEqual(scheduler.next_due, 20.0)

    def test_disarming_requires_a_fresh_quiet_interval(self):
        clock = FakeClock()
        scheduler = IdleLifeScheduler(
            minimum_interval=5,
            maximum_interval=5,
            mutter_chance=0,
            rng=FixedRandom(chance=1.0),
            clock=clock,
        )

        scheduler.poll(armed=True)
        clock.now = 4.0
        self.assertIsNone(scheduler.poll(armed=False))
        self.assertIsNone(scheduler.next_due)

        clock.now = 20.0
        self.assertIsNone(scheduler.poll(armed=True))
        self.assertEqual(scheduler.next_due, 25.0)
        clock.now = 25.0
        self.assertEqual(
            scheduler.poll(armed=True).action,
            IdleAction.EYE_PULSE,
        )

    def test_snooze_restarts_interval_after_foreground_activity(self):
        clock = FakeClock()
        scheduler = IdleLifeScheduler(
            minimum_interval=8,
            maximum_interval=8,
            rng=FixedRandom(),
            clock=clock,
        )
        scheduler.poll(armed=True)
        clock.now = 7.0

        scheduler.snooze()

        self.assertEqual(scheduler.next_due, 15.0)
        clock.now = 14.9
        self.assertIsNone(scheduler.poll(armed=True))

    def test_disabled_scheduler_never_returns_an_action(self):
        clock = FakeClock()
        scheduler = IdleLifeScheduler(
            minimum_interval=1,
            maximum_interval=1,
            enabled=False,
            rng=FixedRandom(),
            clock=clock,
        )

        self.assertIsNone(scheduler.poll(armed=True))
        clock.now = 100.0
        self.assertIsNone(scheduler.poll(armed=True))
        self.assertIsNone(scheduler.next_due)

    def test_no_action_during_active_visit(self):
        """An active visitor visit disarms idle behaviour, so nothing fires."""

        clock, scheduler = make_scheduler(lines=("Still here.",))

        self.assertIsNone(scheduler.poll(armed=True))
        clock.now = 10.0

        # A visitor is present: idle life is disarmed and must not act.
        self.assertIsNone(scheduler.poll(armed=False))
        self.assertIsNone(scheduler.next_due)
        clock.now = 100.0
        self.assertIsNone(scheduler.poll(armed=False))
        self.assertIsNone(scheduler.next_due)

        # Once the visit ends the skeleton re-arms and eventually acts.
        self.assertIsNone(scheduler.poll(armed=True))
        self.assertEqual(scheduler.next_due, 110.0)
        clock.now = 110.0
        self.assertIsNotNone(scheduler.poll(armed=True))

    def test_no_action_while_speech_is_playing(self):
        """Speech playback is foreground work: snooze, then disarm, both hold."""

        clock, scheduler = make_scheduler(lines=("Still here.",))

        self.assertIsNone(scheduler.poll(armed=True))
        self.assertEqual(scheduler.next_due, 10.0)

        # Foreground speech begins: the quiet interval restarts.
        scheduler.snooze()
        self.assertEqual(scheduler.next_due, 10.0)

        # Speech is still playing: idle life is disarmed and must not act,
        # even though the (new) due time has passed.
        clock.now = 20.0
        self.assertIsNone(scheduler.poll(armed=False))
        self.assertIsNone(scheduler.next_due)

        clock.now = 100.0
        self.assertIsNone(scheduler.poll(armed=False))
        self.assertIsNone(scheduler.next_due)

    def test_min_interval_enforced_between_consecutive_actions(self):
        """A fired action cannot fire again before the minimum interval."""

        clock, scheduler = make_scheduler(lines=("Still here.",))

        self.assertIsNone(scheduler.poll(armed=True))
        clock.now = 10.0
        self.assertEqual(
            scheduler.poll(armed=True).action,
            IdleAction.MUTTER,
        )

        # Just before the minimum interval: still waiting.
        clock.now = 19.9
        self.assertIsNone(scheduler.poll(armed=True))
        self.assertEqual(scheduler.next_due, 20.0)

        # At the due time: a second action fires and a new interval starts.
        clock.now = 20.0
        decision = scheduler.poll(armed=True)
        self.assertIsNotNone(decision)
        self.assertEqual(scheduler.next_due, 30.0)

    def test_empty_content_pool_never_mutters_and_does_not_crash(self):
        """With no mutter lines, poll selects a physical action, never crashes."""

        clock, scheduler = make_scheduler(lines=())

        self.assertIsNone(scheduler.poll(armed=True))
        self.assertEqual(scheduler.next_due, 10.0)
        clock.now = 10.0

        decision = scheduler.poll(armed=True)
        self.assertIsNotNone(decision)
        self.assertNotEqual(decision.action, IdleAction.MUTTER)
        self.assertEqual(decision.text, "")

    def test_empty_content_pool_never_fires_when_disarmed(self):
        """No crash and no action from an empty pool while disarmed."""

        clock, scheduler = make_scheduler(lines=())
        clock.now = 100.0
        self.assertIsNone(scheduler.poll(armed=False))
        self.assertIsNone(scheduler.next_due)


if __name__ == "__main__":
    unittest.main()
