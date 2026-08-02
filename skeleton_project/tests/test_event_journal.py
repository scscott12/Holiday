import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from holiday_skeleton.event_journal import (
    EventJournal,
    EventJournalError,
    MAX_DETAIL_CHARS,
    sanitize_detail,
)


class EventJournalTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.path = Path(self.temporary.name) / "diagnostic-events.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_session_round_trip_uses_private_atomic_file(self):
        journal = EventJournal(self.path, maximum_entries=32)

        opened = journal.start_session()
        recorded = journal.record(
            "content",
            "reload_failed",
            "error",
            "controller",
            "malformed scene file",
        )
        closed = journal.end_session()

        self.assertIsNotNone(opened.active_session)
        self.assertEqual(recorded.last_event.code, "reload_failed")
        self.assertIsNone(closed.active_session)
        self.assertEqual(closed.last_event.code, "runtime_stopped")
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)

        restored = EventJournal(self.path, maximum_entries=32).load()
        self.assertEqual(restored.events, closed.events)
        self.assertIsNone(restored.active_session)

    def test_open_session_detects_unclean_previous_runtime(self):
        first = EventJournal(self.path, maximum_entries=32)
        first.start_session()
        first.record("watchdog", "watchdog_stale", "error", "watchdog")

        second = EventJournal(self.path, maximum_entries=32)
        snapshot = second.start_session()

        self.assertEqual(
            [event.code for event in snapshot.events[-2:]],
            ["unclean_restart", "runtime_started"],
        )
        self.assertEqual(snapshot.events[-2].severity, "warning")

    def test_clean_previous_runtime_does_not_report_unclean_restart(self):
        first = EventJournal(self.path, maximum_entries=32)
        first.start_session()
        first.end_session()

        second = EventJournal(self.path, maximum_entries=32)
        snapshot = second.start_session()

        self.assertEqual(snapshot.events[-1].code, "runtime_started")
        self.assertEqual(
            sum(event.code == "unclean_restart" for event in snapshot.events),
            0,
        )

    def test_clean_session_close_is_idempotent(self):
        journal = EventJournal(self.path, maximum_entries=32)
        journal.start_session()

        first = journal.end_session()
        second = journal.end_session()

        self.assertEqual(first, second)
        self.assertEqual(
            sum(event.code == "runtime_stopped" for event in second.events),
            1,
        )

    def test_retention_is_bounded_and_sequences_continue(self):
        journal = EventJournal(self.path, maximum_entries=16)
        journal.start_session()
        for index in range(30):
            journal.record(
                "health",
                "component_failed",
                "error",
                "microphone",
                f"failure {index}",
            )

        snapshot = journal.snapshot()

        self.assertEqual(snapshot.count, 16)
        self.assertEqual(snapshot.events[-1].sequence, 31)
        self.assertEqual(snapshot.next_sequence, 32)
        self.assertGreater(snapshot.events[0].sequence, 1)

    def test_concurrent_writers_keep_unique_ordered_events(self):
        journal = EventJournal(self.path, maximum_entries=128)
        journal.start_session()
        failures = []

        def writer(index):
            try:
                for item in range(8):
                    journal.record(
                        "health",
                        "state_changed",
                        "info",
                        "monitor",
                        f"writer {index} event {item}",
                    )
            except Exception as error:
                failures.append(error)

        threads = [threading.Thread(target=writer, args=(index,)) for index in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

        self.assertFalse(failures)
        snapshot = EventJournal(self.path, maximum_entries=128).load()
        sequences = [event.sequence for event in snapshot.events]
        self.assertEqual(len(snapshot.events), 33)
        self.assertEqual(sequences, sorted(set(sequences)))

    def test_failed_atomic_replace_preserves_prior_file_and_memory(self):
        journal = EventJournal(self.path, maximum_entries=32)
        before = journal.start_session()
        original = self.path.read_bytes()

        with mock.patch(
            "holiday_skeleton.event_journal.os.replace",
            side_effect=OSError("read-only filesystem"),
        ):
            with self.assertRaisesRegex(EventJournalError, "read-only filesystem"):
                journal.record(
                    "settings",
                    "settings_failed",
                    "error",
                    "controller",
                )

        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(journal.snapshot(), before)

    def test_credentials_are_redacted_before_persistence(self):
        journal = EventJournal(self.path, maximum_entries=32)
        journal.start_session()

        snapshot = journal.record(
            "mqtt",
            "connection_failed",
            "error",
            "mqtt",
            "password='hunter two' username=alice Bearer abc.def "
            "mqtt://alice:secret@broker token:xyz",
        )

        detail = snapshot.last_event.detail
        self.assertNotIn("hunter two", detail)
        self.assertNotIn("username=alice", detail)
        self.assertNotIn("abc.def", detail)
        self.assertNotIn("alice:secret", detail)
        self.assertNotIn("xyz", detail)
        self.assertIn("[redacted]", detail)
        self.assertNotIn("hunter2", self.path.read_text(encoding="utf-8"))

    def test_detail_is_single_line_and_bounded(self):
        detail = sanitize_detail("line one\nline two " + "x" * 500)

        self.assertNotIn("\n", detail)
        self.assertEqual(len(detail), MAX_DETAIL_CHARS)

    def test_recent_payload_is_bounded_and_strict_json(self):
        journal = EventJournal(self.path, maximum_entries=32)
        journal.start_session()
        for index in range(8):
            journal.record("runtime", "state_changed", source="controller", detail=index)

        payload = json.loads(journal.snapshot().recent_json(limit=3))

        self.assertEqual(len(payload["events"]), 3)
        self.assertEqual(payload["retained"], 9)
        self.assertEqual(payload["maximum_entries"], 32)

    def test_corrupt_document_is_rejected_without_replacement(self):
        self.path.write_text('{"version": 1, "events": [', encoding="utf-8")
        original = self.path.read_bytes()

        with self.assertRaisesRegex(EventJournalError, "cannot parse"):
            EventJournal(self.path).start_session()

        self.assertEqual(self.path.read_bytes(), original)

    def test_unknown_fields_and_invalid_tokens_are_rejected(self):
        journal = EventJournal(self.path)
        journal.start_session()

        with self.assertRaisesRegex(EventJournalError, "safe lowercase token"):
            journal.record("runtime", "bad code with spaces")

        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["visitor_transcript"] = "must not be accepted"
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(EventJournalError, "unknown fields"):
            EventJournal(self.path).load()

    def test_lower_retention_setting_keeps_newest_valid_events(self):
        journal = EventJournal(self.path, maximum_entries=32)
        journal.start_session()
        for index in range(20):
            journal.record("runtime", "state_changed", detail=index)

        restored = EventJournal(self.path, maximum_entries=16).load()

        self.assertEqual(restored.count, 16)
        self.assertEqual(restored.events[-1].detail, "19")

    def test_existing_credential_text_is_rejected_before_publication(self):
        journal = EventJournal(self.path)
        journal.start_session()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["events"][-1]["detail"] = "token=must-not-load"
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(EventJournalError, "unsafe credential"):
            EventJournal(self.path).load()

    def test_timezone_free_timestamps_are_rejected(self):
        journal = EventJournal(self.path)
        journal.start_session()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        payload["updated_at"] = "2026-08-02T12:00:00"
        self.path.write_text(json.dumps(payload), encoding="utf-8")

        with self.assertRaisesRegex(EventJournalError, "include a timezone"):
            EventJournal(self.path).load()


if __name__ == "__main__":
    unittest.main()
