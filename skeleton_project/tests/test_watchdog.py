import unittest
from pathlib import Path

from holiday_skeleton.watchdog import (
    ControllerWatchdog,
    SystemdNotifier,
    WatchdogState,
)


class FakeClock:
    def __init__(self):
        self.value = 100.0

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class SystemdNotifierTests(unittest.TestCase):
    def test_ready_watchdog_and_stopping_use_notify_protocol(self):
        sent = []
        notifier = SystemdNotifier(
            "/run/systemd/notify",
            60.0,
            sender=lambda address, payload: sent.append((address, payload)),
        )

        self.assertTrue(notifier.ready("controller ready"))
        self.assertTrue(notifier.watchdog())
        self.assertTrue(notifier.stopping("service stopping"))

        self.assertEqual(sent[0][0], "/run/systemd/notify")
        self.assertEqual(sent[0][1], b"READY=1\nSTATUS=controller ready")
        self.assertEqual(sent[1][1], b"WATCHDOG=1")
        self.assertEqual(sent[2][1], b"STOPPING=1\nSTATUS=service stopping")

    def test_abstract_notify_socket_is_encoded_for_linux_namespace(self):
        sent = []
        notifier = SystemdNotifier(
            "@notify-test",
            sender=lambda address, payload: sent.append((address, payload)),
        )

        notifier.status("warming")

        self.assertEqual(sent, [(b"\0notify-test", b"STATUS=warming")])

    def test_environment_requires_matching_watchdog_pid(self):
        values = {
            "NOTIFY_SOCKET": "/run/systemd/notify",
            "WATCHDOG_USEC": "60000000",
            "WATCHDOG_PID": "42",
        }

        mismatch = SystemdNotifier.from_environment(values, pid=41)
        match = SystemdNotifier.from_environment(values, pid=42)

        self.assertFalse(mismatch.watchdog_enabled)
        self.assertTrue(match.watchdog_enabled)
        self.assertEqual(match.watchdog_timeout_seconds, 60.0)

    def test_bad_notification_does_not_escape(self):
        notifier = SystemdNotifier(
            "/run/systemd/notify",
            60.0,
            sender=lambda _address, _payload: (_ for _ in ()).throw(OSError("gone")),
        )

        self.assertFalse(notifier.watchdog())
        self.assertEqual(notifier.last_error, "gone")


class ControllerWatchdogTests(unittest.TestCase):
    def make_watchdog(self):
        sent = []
        clock = FakeClock()
        snapshots = []
        notifier = SystemdNotifier(
            "/run/systemd/notify",
            60.0,
            sender=lambda address, payload: sent.append((address, payload)),
        )
        watchdog = ControllerWatchdog(
            notifier,
            stale_after_seconds=45.0,
            changed=snapshots.append,
            clock=clock,
        )
        return watchdog, clock, sent, snapshots

    def test_fresh_controller_heartbeat_feeds_systemd(self):
        watchdog, _clock, sent, snapshots = self.make_watchdog()
        watchdog.pulse("idle")

        self.assertTrue(watchdog.feed_once())

        snapshot = watchdog.snapshot()
        self.assertEqual(snapshot.state, WatchdogState.READY)
        self.assertEqual(snapshot.controller_state, "idle")
        self.assertEqual(snapshot.feed_count, 1)
        self.assertEqual(sent[-1][1], b"WATCHDOG=1")
        self.assertEqual(snapshots[-1].state, WatchdogState.READY)

    def test_stale_controller_withholds_feed_then_recovers_on_progress(self):
        watchdog, clock, sent, _snapshots = self.make_watchdog()
        watchdog.pulse("speaking")
        self.assertTrue(watchdog.feed_once())
        initial_messages = len(sent)

        clock.advance(46.0)
        self.assertFalse(watchdog.feed_once())
        self.assertEqual(len(sent), initial_messages)
        self.assertEqual(watchdog.snapshot().state, WatchdogState.STALE)
        self.assertIn("stale", watchdog.snapshot().last_error)

        watchdog.pulse("idle")
        self.assertTrue(watchdog.feed_once())
        self.assertEqual(watchdog.snapshot().state, WatchdogState.READY)
        self.assertEqual(watchdog.snapshot().feed_count, 2)

    def test_no_controller_heartbeat_never_fakes_liveness(self):
        watchdog, _clock, sent, _snapshots = self.make_watchdog()

        self.assertFalse(watchdog.feed_once())

        self.assertEqual(sent, [])
        self.assertEqual(watchdog.snapshot().state, WatchdogState.STARTING)
        self.assertEqual(
            watchdog.snapshot().last_error,
            "waiting for controller heartbeat",
        )

    def test_missing_systemd_environment_is_a_neutral_disabled_state(self):
        watchdog = ControllerWatchdog.from_environment(environ={})

        watchdog.pulse("idle")
        self.assertFalse(watchdog.feed_once())
        self.assertFalse(watchdog.enabled)
        self.assertEqual(watchdog.snapshot().state, WatchdogState.DISABLED)

    def test_stale_limit_is_clamped_below_systemd_deadline(self):
        notifier = SystemdNotifier(
            "/run/systemd/notify",
            60.0,
            sender=lambda _address, _payload: None,
        )

        watchdog = ControllerWatchdog(notifier, stale_after_seconds=999.0)

        self.assertEqual(watchdog.stale_after_seconds, 54.0)

    def test_failed_ready_notification_is_retried_before_watchdog_feed(self):
        sent = []
        attempts = 0

        def flaky_sender(_address, payload):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise OSError("temporary notify failure")
            sent.append(payload)

        notifier = SystemdNotifier("/run/systemd/notify", 60.0, flaky_sender)
        watchdog = ControllerWatchdog(notifier, stale_after_seconds=45.0)
        watchdog.pulse("idle")

        self.assertFalse(watchdog.ready("controller ready"))
        self.assertTrue(watchdog.feed_once())

        self.assertEqual(
            sent,
            [b"READY=1\nSTATUS=controller ready", b"WATCHDOG=1"],
        )
        self.assertEqual(watchdog.snapshot().state, WatchdogState.READY)

    def test_packaged_service_enables_notify_watchdog_and_restart(self):
        service = (
            Path(__file__).resolve().parents[1]
            / "systemd"
            / "holiday-skeleton.service"
        ).read_text(encoding="utf-8")

        self.assertIn("Type=notify", service)
        self.assertIn("NotifyAccess=main", service)
        self.assertIn("WatchdogSec=60", service)
        self.assertIn("Restart=always", service)


if __name__ == "__main__":
    unittest.main()
