import tempfile
import unittest
from pathlib import Path
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.event_journal import EventJournal, EventJournalError
from holiday_skeleton.health import ComponentState
from holiday_skeleton.watchdog import WatchdogSnapshot, WatchdogState


class RuntimeEventJournalTests(unittest.TestCase):
    def test_invalid_and_out_of_range_retention_values_are_safe(self):
        with mock.patch.object(runtime, "envs", return_value="not-an-integer"):
            self.assertEqual(runtime.bounded_env_int("TEST", 128, 16, 512), 128)
        with mock.patch.object(runtime, "envs", return_value="9999"):
            self.assertEqual(runtime.bounded_env_int("TEST", 128, 16, 512), 512)

    def test_initialization_opens_persistent_session_and_publishes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-events.json"
            published = []
            with mock.patch.multiple(
                runtime,
                EVENT_JOURNAL_ENABLED=True,
                EVENT_JOURNAL_PATH=str(path),
                EVENT_JOURNAL_MAX_ENTRIES=32,
                _event_journal=None,
                _event_journal_state="starting",
                _event_journal_last_error="none",
                _health=None,
                mqtt_pub=lambda topic, payload, retain=False: published.append(
                    (topic, payload, retain)
                ),
            ):
                runtime._init_event_journal()

                self.assertEqual(runtime._event_journal_state, "ready")
                self.assertEqual(
                    runtime._event_journal.snapshot().last_event.code,
                    "runtime_started",
                )
                self.assertTrue(path.is_file())
                self.assertIn(("journal/state", "ready", True), published)

    def test_disabled_journal_is_neutral(self):
        published = []
        with mock.patch.multiple(
            runtime,
            EVENT_JOURNAL_ENABLED=False,
            _event_journal=object(),
            _health=None,
            mqtt_pub=lambda topic, payload, retain=False: published.append(
                (topic, payload)
            ),
        ):
            runtime._init_event_journal()

            self.assertIsNone(runtime._event_journal)
            self.assertEqual(runtime._event_journal_state, "disabled")
            self.assertIn(("journal/count", "0"), published)

    def test_close_marks_session_clean_before_process_exit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic-events.json"
            journal = EventJournal(path, maximum_entries=32)
            journal.start_session()
            with mock.patch.multiple(
                runtime,
                _event_journal=journal,
                _event_journal_state="ready",
                _event_journal_last_error="none",
                _publish_event_journal_snapshot=mock.DEFAULT,
            ):
                runtime._close_event_journal()

                self.assertEqual(runtime._event_journal_state, "stopped")
                self.assertIsNone(journal.snapshot().active_session)
                self.assertEqual(journal.snapshot().last_event.code, "runtime_stopped")

    def test_write_failure_degrades_only_journal_component(self):
        journal = mock.Mock()
        journal.record.side_effect = EventJournalError("filesystem is read-only")
        health = mock.Mock()
        with mock.patch.multiple(
            runtime,
            _event_journal=journal,
            _event_journal_state="ready",
            _event_journal_last_error="none",
            _health=health,
            _publish_event_journal_snapshot=mock.DEFAULT,
        ) as patched:
            result = runtime._event_journal_record(
                "content",
                "reload_failed",
                "error",
                "controller",
            )

            self.assertFalse(result)
            self.assertEqual(runtime._event_journal_state, "error")
            health.set_component.assert_called_once_with(
                "event_journal",
                ComponentState.DEGRADED,
                "filesystem is read-only",
                False,
                publish=False,
            )
            patched["_publish_event_journal_snapshot"].assert_called_once_with()

    def test_health_failures_are_deduplicated_and_recovery_is_recorded(self):
        record = mock.Mock()
        states = {}
        with mock.patch.multiple(
            runtime,
            _health=None,
            _event_journal_health_states=states,
            _event_journal_record=record,
        ):
            runtime._health_set("microphone", ComponentState.FAILED, "capture failed")
            runtime._health_set("microphone", ComponentState.FAILED, "capture failed")
            runtime._health_set("microphone", ComponentState.READY, "input ready")

        self.assertEqual(record.call_count, 2)
        self.assertEqual(record.call_args_list[0].args[:3], (
            "health",
            "component_failed",
            "error",
        ))
        self.assertEqual(record.call_args_list[1].args[:3], (
            "health",
            "component_recovered",
            "info",
        ))

    def test_watchdog_journal_records_state_transitions_not_each_feed(self):
        record = mock.Mock()
        snapshot = WatchdogSnapshot(
            enabled=True,
            state=WatchdogState.STALE,
            controller_age_seconds=46.0,
            timeout_seconds=60.0,
            stale_after_seconds=45.0,
            feed_count=3,
            last_feed="2026-08-02T00:00:00+00:00",
            last_error="controller heartbeat stale for 46.0s",
            controller_state="speaking",
        )
        with mock.patch.multiple(
            runtime,
            _event_journal_watchdog_state=WatchdogState.READY.value,
            _event_journal_record=record,
            _health_set=mock.DEFAULT,
            _publish_watchdog_snapshot=mock.DEFAULT,
        ):
            runtime._watchdog_changed(snapshot)
            runtime._watchdog_changed(snapshot)

        record.assert_called_once_with(
            "watchdog",
            "watchdog_stale",
            "error",
            "watchdog",
            "controller heartbeat stale for 46.0s",
        )

    def test_maintenance_blocked_command_is_journaled_without_payload(self):
        record = mock.Mock()
        with mock.patch.multiple(
            runtime,
            _maintenance_rejected_count=0,
            _event_journal_record=record,
            _record_maintenance_result=mock.DEFAULT,
        ):
            runtime._maintenance_reject("speech")

        record.assert_called_once_with(
            "maintenance",
            "command_blocked",
            "warning",
            "controller",
            "blocked action speech",
        )

    def test_published_recent_history_contains_no_conversation_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            journal = EventJournal(Path(directory) / "events.json", maximum_entries=32)
            snapshot = journal.start_session()
            published = {}
            with mock.patch.multiple(
                runtime,
                _event_journal=journal,
                _event_journal_state="ready",
                _event_journal_last_error="none",
                mqtt_pub=lambda topic, payload, retain=False: published.__setitem__(
                    topic, payload
                ),
            ):
                runtime._publish_event_journal_snapshot(snapshot)

            recent = published["journal/recent"]
            self.assertNotIn("transcript", recent)
            self.assertNotIn("prompt", recent)
            self.assertNotIn("recognized_speech", recent)

    def test_normal_transcript_flow_never_calls_event_journal(self):
        record = mock.Mock()
        with mock.patch.multiple(
            runtime,
            _event_journal_record=record,
            mqtt_pub=mock.DEFAULT,
        ):
            runtime._transcript_start()
            runtime._transcript_add("user", "visitor sentence must stay ephemeral")
            runtime._transcript_add("assistant", "generated reply must stay ephemeral")
            runtime._transcript_publish_and_clear()

        record.assert_not_called()


if __name__ == "__main__":
    unittest.main()
