import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import skeleton_all_in_one_mqtt as runtime
from holiday_skeleton.content import ContentReloadError, ContentReloadInterrupted
from holiday_skeleton.controller import RuntimeState
from holiday_skeleton.personality import PersonalityLibrary
from holiday_skeleton.scene import SceneLibrary
from holiday_skeleton.speech import SpeechCacheMetrics


class FakeController:
    def __init__(self, state=RuntimeState.IDLE):
        self.state = state
        self.content_reload_interrupt_event = threading.Event()
        self.stop_event = threading.Event()
        self.states = []
        self.heartbeats = 0

    def set_state(self, state):
        self.state = state
        self.states.append(state)

    def heartbeat(self):
        self.heartbeats += 1


class FakeSpeechEngine:
    sample_rate = 22050

    def __init__(self):
        self.cached = []
        self.retained = []

    def cache_phrases(self, lines, stop_event=None, progress=None):
        values = list(lines)
        if progress is not None:
            progress()
        self.cached.append(values)
        return SpeechCacheMetrics(
            requested_entries=len(values),
            new_entries=len(values),
            existing_entries=0,
            failed_entries=0,
            total_entries=len(values),
            warmup_seconds=0.01,
            audio_seconds=0.25,
            pcm_bytes=4096,
            errors=(),
            interrupted=bool(stop_event is not None and stop_event.is_set()),
        )

    def retain_cached_phrases(self, lines):
        self.retained.append(list(lines))
        return 0

    @property
    def cache_entries(self):
        return len(self.retained[-1]) if self.retained else 0

    @property
    def cache_pcm_bytes(self):
        return self.cache_entries * 100


class RuntimeContentReloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.personalities = PersonalityLibrary.load(runtime.PERSONALITIES_PATH)
        cls.scenes = SceneLibrary.load(runtime.SCENES_PATH)

    def _patch_runtime(self, **overrides):
        pirate = self.personalities.select("pirate")
        defaults = dict(
            controller=FakeController(),
            _personality_library=self.personalities,
            _personality_active=pirate,
            _personality_load_error="",
            _scene_library=self.scenes,
            _scene_runner=object(),
            _scene_sound_cache={},
            _scene_sound_errors={},
            _scene_load_error="",
            _speech_engine=FakeSpeechEngine(),
            _content_reload_active=False,
            _content_reload_pending=True,
            _content_reload_state="ready",
            _content_reload_last_result="queued",
            _content_reload_last_error="none",
            _content_reload_last_run="never",
            _content_reload_last_duration=0.0,
            _content_reload_count=0,
            _content_reload_interrupted=0,
            _health_set=mock.DEFAULT,
            mqtt_pub=mock.DEFAULT,
            publish_mqtt_discovery=mock.DEFAULT,
            _publish_barge_in_capability=mock.DEFAULT,
            _publish_idle_life_ready_state=mock.DEFAULT,
        )
        defaults.update(overrides)
        return mock.patch.multiple(runtime, **defaults)

    def test_successful_reload_swaps_both_libraries_after_preload(self):
        old_library = self.personalities
        old_scenes = self.scenes
        speech = FakeSpeechEngine()
        runtime._apply_personality_globals(old_library.select("pirate"))

        with self._patch_runtime(_speech_engine=speech):
            runtime._run_content_reload()

            self.assertIsNot(runtime._personality_library, old_library)
            self.assertIsNot(runtime._scene_library, old_scenes)
            self.assertEqual(runtime._personality_active.name, "pirate")
            self.assertEqual(runtime._personality_library.names, old_library.names)
            self.assertEqual(runtime._scene_library.names, old_scenes.names)
            self.assertEqual(runtime._content_reload_last_result, "reloaded")
            self.assertEqual(runtime._content_reload_state, "ready")
            self.assertEqual(runtime._content_reload_count, 1)
            self.assertEqual(len(speech.cached), 1)
            self.assertEqual(len(speech.retained), 1)
            self.assertIn("The sun be up already. Bold of it, honestly.", speech.retained[0])
            self.assertIn(
                "Careful where you step. Some of us are still using these bones.",
                speech.retained[0],
            )

    def test_invalid_candidate_keeps_every_active_reference(self):
        old_pack = self.personalities.select("pirate")
        old_runner = object()
        old_client = object()
        old_matcher = object()
        old_idle = object()
        with self._patch_runtime(
            _scene_runner=old_runner,
            _llm_client=old_client,
            _barge_in_matcher=old_matcher,
            _idle_life=old_idle,
        ), mock.patch.object(
            runtime,
            "prepare_content",
            side_effect=ContentReloadError("malformed scene file"),
        ):
            runtime._run_content_reload()

            self.assertIs(runtime._personality_library, self.personalities)
            self.assertIs(runtime._personality_active, old_pack)
            self.assertIs(runtime._scene_library, self.scenes)
            self.assertIs(runtime._scene_runner, old_runner)
            self.assertIs(runtime._llm_client, old_client)
            self.assertIs(runtime._barge_in_matcher, old_matcher)
            self.assertIs(runtime._idle_life, old_idle)
            self.assertEqual(runtime._content_reload_last_result, "error")
            self.assertIn("malformed scene file", runtime._content_reload_last_error)

    def test_post_commit_status_failure_does_not_claim_content_rolled_back(self):
        old_library = self.personalities
        with self._patch_runtime(
            publish_mqtt_discovery=mock.Mock(
                side_effect=RuntimeError("broker publication failed")
            ),
        ):
            runtime._run_content_reload()

            self.assertIsNot(runtime._personality_library, old_library)
            self.assertEqual(runtime._content_reload_last_result, "reloaded")
            self.assertIn("content committed", runtime._content_reload_last_error)

    def test_interrupted_candidate_keeps_active_content_without_degrading(self):
        with self._patch_runtime(), mock.patch.object(
            runtime,
            "prepare_content",
            side_effect=ContentReloadInterrupted("visitor arrived"),
        ):
            runtime._run_content_reload()

            self.assertIs(runtime._personality_library, self.personalities)
            self.assertIs(runtime._scene_library, self.scenes)
            self.assertEqual(runtime._content_reload_last_result, "interrupted")
            self.assertEqual(runtime._content_reload_state, "ready")
            self.assertEqual(runtime._content_reload_interrupted, 1)

    def test_request_is_rejected_while_visitor_is_speaking(self):
        published = []
        with mock.patch.multiple(
            runtime,
            controller=SimpleNamespace(state=RuntimeState.SPEAKING),
            _content_reload_active=False,
            _content_reload_pending=False,
            mqtt_pub=lambda topic, payload, retain=False: published.append((topic, payload)),
            _enqueue=mock.DEFAULT,
        ) as patched:
            accepted = runtime._request_content_reload()

        self.assertFalse(accepted)
        patched["_enqueue"].assert_not_called()
        self.assertIn(("content_reload/last_result", "busy"), published)
        self.assertIn(("content_reload/last_error", "controller is speaking"), published)

    def test_request_is_allowed_during_maintenance_lockout(self):
        enqueue = mock.Mock(return_value=True)
        with mock.patch.multiple(
            runtime,
            controller=SimpleNamespace(state=RuntimeState.MAINTENANCE),
            _content_reload_active=False,
            _content_reload_pending=False,
            mqtt_pub=mock.DEFAULT,
            _enqueue=enqueue,
        ):
            accepted = runtime._request_content_reload()

        self.assertTrue(accepted)
        enqueue.assert_called_once_with(
            runtime.EventKind.RELOAD_CONTENT,
            source="mqtt",
        )


if __name__ == "__main__":
    unittest.main()
