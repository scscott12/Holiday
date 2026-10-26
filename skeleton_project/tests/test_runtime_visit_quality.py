import unittest
from unittest import mock

import skeleton_all_in_one_mqtt as runtime


class TranscriptNotificationTests(unittest.TestCase):
    def _publish(self, utterances):
        published = []
        with mock.patch.object(runtime, "mqtt_pub", lambda t, p, retain=False: published.append((t, p))):
            runtime._transcript_start()
            for role, text in utterances:
                runtime._transcript_add(role, text)
            runtime._transcript_publish_and_clear()
        return published

    def test_greeting_and_goodbye_alone_publish_nothing(self):
        self.assertEqual(self._publish([("assistant", "Hi"), ("assistant", "Bye")]), [])

    def test_visitor_speech_is_included(self):
        published = dict(self._publish([("assistant", "Hi"), ("user", "trick or treat"), ("assistant", "Bye")]))
        self.assertIn('**Visitor**: "trick or treat"', published["transcript"])

    def test_long_transcript_is_shortened_for_state_with_full_text_attribute(self):
        published = dict(self._publish([("user", "x" * 400)]))
        self.assertLessEqual(len(published["transcript"]), runtime.HA_STATE_MAX)
        self.assertIn("x" * 400, published["transcript/attributes"])


class EmptyVisitBackoffTests(unittest.TestCase):
    def test_cooldown_doubles_after_empty_visits_and_resets_on_interaction(self):
        with mock.patch.multiple(runtime, MOTION_COOLDOWN_SEC=30.0, EMPTY_VISIT_BACKOFF_MAX_SEC=300.0, _empty_visits=0, mqtt_pub=lambda *a, **k: None):
            self.assertEqual(runtime._effective_cooldown(), 30.0)
            runtime._note_visit_result(False)
            self.assertEqual(runtime._effective_cooldown(), 60.0)
            for _ in range(10):
                runtime._note_visit_result(False)
            self.assertEqual(runtime._effective_cooldown(), 300.0)
            runtime._note_visit_result(True)
            self.assertEqual(runtime._effective_cooldown(), 30.0)


class LinePickingTests(unittest.TestCase):
    def test_pick_fresh_avoids_immediate_repeat(self):
        with mock.patch.object(runtime, "_last_lines", {}):
            lines = ["a", "b"]
            previous = runtime._pick_fresh("t", lines)
            for _ in range(20):
                current = runtime._pick_fresh("t", lines)
                self.assertNotEqual(current, previous)
                previous = current


if __name__ == "__main__":
    unittest.main()
