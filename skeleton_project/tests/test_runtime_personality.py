import unittest
from types import SimpleNamespace
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.controller import RuntimeState
from holiday_skeleton.personality import PersonalityLibrary
from holiday_skeleton.scene import SceneLibrary
from holiday_skeleton.speech import SpeechCacheMetrics


class FakeSpeechEngine:
    def __init__(self):
        self.cached = []
        self.retained = []

    def cache_phrases(self, lines):
        lines = list(lines)
        self.cached.append(lines)
        return SpeechCacheMetrics(
            requested_entries=len(lines),
            new_entries=len(lines),
            existing_entries=0,
            failed_entries=0,
            total_entries=len(lines),
            warmup_seconds=0.01,
            audio_seconds=0.25,
            pcm_bytes=4096,
            errors=(),
        )

    def retain_cached_phrases(self, lines):
        self.retained.append(list(lines))
        return 1

    @property
    def cache_entries(self):
        return len(self.retained[-1]) if self.retained else 0

    @property
    def cache_pcm_bytes(self):
        return self.cache_entries * 100


class RuntimePersonalityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.personalities = PersonalityLibrary.load(runtime.PERSONALITIES_PATH)
        cls.scenes = SceneLibrary.load(runtime.SCENES_PATH)

    def test_switch_rebuilds_dependent_runtime_and_prunes_cache(self):
        speech = FakeSpeechEngine()
        pirate = self.personalities.select("pirate")
        persisted = []
        runtime._apply_personality_globals(pirate)

        with mock.patch.multiple(
            runtime,
            _personality_library=self.personalities,
            _personality_active=pirate,
            _scene_library=self.scenes,
            _speech_engine=speech,
            _personality_switch_count=0,
            _publish_barge_in_capability=mock.DEFAULT,
            _publish_idle_life_ready_state=mock.DEFAULT,
            _publish_memory_turns=mock.DEFAULT,
            _persist_operator_settings=lambda: persisted.append(runtime._personality_active.name),
            _health_set=mock.DEFAULT,
            mqtt_pub=mock.DEFAULT,
        ):
            runtime._switch_personality("silent_watcher")

            self.assertEqual(runtime._personality_active.name, "silent_watcher")
            self.assertEqual(runtime.LLM_MEMORY_TURNS, 2)
            self.assertEqual(runtime.LLM_CONTEXT_TOKENS, 384)
            self.assertEqual(runtime.PERSONALITY_VOLUME_MULTIPLIER, 0.72)
            self.assertIn("watcher", runtime._barge_in_matcher.grammar)
            self.assertEqual(runtime._idle_life.mutter_lines, tuple(runtime.IDLE_LINES))
            self.assertEqual(runtime._personality_switch_count, 1)
            self.assertEqual(runtime._personality_last_result, "switched")
            self.assertEqual(len(speech.cached), 1)
            self.assertEqual(len(speech.retained), 1)
            self.assertIn("There you are.", speech.retained[0])
            self.assertNotIn("The sun be up already. Bold of it, honestly.", speech.retained[0])
            self.assertEqual(persisted, ["silent_watcher"])

    def test_switch_request_is_rejected_during_active_visit(self):
        published = []
        controller = SimpleNamespace(state=RuntimeState.SPEAKING)
        with mock.patch.multiple(
            runtime,
            controller=controller,
            mqtt_pub=lambda topic, payload, retain=False: published.append((topic, payload)),
            _enqueue=mock.DEFAULT,
        ) as patched:
            accepted = runtime._request_personality_switch("graveyard_host")

        self.assertFalse(accepted)
        patched["_enqueue"].assert_not_called()
        self.assertIn(("personality/last_result", "busy"), published)
        self.assertIn(
            ("personality/last_error", "cannot switch while runtime is speaking"),
            published,
        )

    def test_missing_default_scene_cannot_partially_switch_pack(self):
        pirate = self.personalities.select("pirate")
        limited_scenes = SimpleNamespace(get=lambda _name: None)
        with mock.patch.multiple(
            runtime,
            _personality_library=self.personalities,
            _personality_active=pirate,
            _scene_library=limited_scenes,
            _speech_engine=None,
            _health_set=mock.DEFAULT,
            mqtt_pub=mock.DEFAULT,
        ):
            runtime._switch_personality("graveyard_host")

            self.assertEqual(runtime._personality_active.name, "pirate")
            self.assertEqual(runtime._personality_last_result, "error")
            self.assertIn("unknown default scene", runtime._personality_last_error)

    def test_legacy_selection_is_a_noop_when_packs_are_unavailable(self):
        published = []
        with mock.patch.multiple(
            runtime,
            _personality_library=None,
            mqtt_pub=lambda topic, payload, retain=False: published.append((topic, payload)),
        ):
            accepted = runtime._request_personality_switch("legacy")

        self.assertTrue(accepted)
        self.assertIn(("personality/last_result", "unchanged"), published)
        self.assertIn(("personality/last_error", "none"), published)


if __name__ == "__main__":
    unittest.main()
