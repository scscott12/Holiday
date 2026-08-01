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


if __name__ == "__main__":
    unittest.main()
