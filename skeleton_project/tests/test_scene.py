import json
import tempfile
import threading
import unittest
import wave
from pathlib import Path

import numpy as np

from holiday_skeleton.scene import (
    SceneAction,
    SceneConfigError,
    SceneLibrary,
    SceneRunner,
    load_wav_pcm16,
    resolve_sound_path,
)


def scene_data(steps):
    return {
        "version": 1,
        "scenes": {
            "test_scene": {
                "description": "Test",
                "steps": steps,
            }
        },
    }


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class SceneLibraryTests(unittest.TestCase):
    def test_packaged_default_scenes_are_valid(self):
        library = SceneLibrary.load(Path(__file__).resolve().parents[1] / "scenes.json")

        self.assertEqual(
            library.names,
            ("awakening", "warning", "silent_scare"),
        )

    def test_loads_and_normalizes_every_supported_action(self):
        library = SceneLibrary.from_data(scene_data([
            {"action": "speak", "text": "  Ahoy.  "},
            {"action": "pause", "duration_ms": 250},
            {"action": "eyes", "level": 0.4},
            {"action": "blink", "count": 2, "period_ms": 100},
            {"action": "flicker", "duration": 1, "base": 0.1, "span": 0.5},
            {"action": "jaw", "level": 0.5, "duration_ms": 120},
            {"action": "sound", "file": "chains/rattle.wav", "jaw": True},
        ]))

        scene = library.get(" TEST_SCENE ")

        self.assertEqual(library.names, ("test_scene",))
        self.assertEqual(
            tuple(step.action for step in scene.steps),
            tuple(SceneAction),
        )
        self.assertEqual(scene.steps[0].parameters["text"], "Ahoy.")
        self.assertEqual(scene.steps[1].parameters["duration"], 0.25)
        self.assertEqual(library.referenced_sounds, ("chains/rattle.wav",))

    def test_loads_from_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenes.json"
            path.write_text(json.dumps(scene_data([
                {"action": "pause", "duration": 0.1},
            ])), encoding="utf-8")

            library = SceneLibrary.load(path)

        self.assertEqual(library.names, ("test_scene",))

    def test_rejects_path_traversal_and_absolute_sound_paths(self):
        for value in ("../secret.wav", "/tmp/secret.wav", "not-wave.mp3"):
            with self.subTest(value=value):
                with self.assertRaises(SceneConfigError):
                    SceneLibrary.from_data(scene_data([
                        {"action": "sound", "file": value},
                    ]))

    def test_rejects_unknown_actions_and_unbounded_values(self):
        invalid_steps = (
            {"action": "shell", "command": "anything"},
            {"action": "pause", "duration": 31},
            {"action": "eyes", "level": 1.5},
            {"action": "blink", "count": 100},
            {"action": "sound", "file": "cue.wav", "jaw": "false"},
        )
        for step in invalid_steps:
            with self.subTest(step=step):
                with self.assertRaises(SceneConfigError):
                    SceneLibrary.from_data(scene_data([step]))

    def test_resolve_sound_path_stays_under_configured_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            resolved = resolve_sound_path(root, "chains/rattle.wav")

            self.assertEqual(resolved, root / "chains" / "rattle.wav")

    def test_pcm_wav_is_mixed_to_mono_and_resampled(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stereo.wav"
            left = np.array([1000, 2000, 3000, 4000], dtype=np.int16)
            right = np.array([-1000, 0, 1000, 2000], dtype=np.int16)
            stereo = np.column_stack((left, right)).reshape(-1)
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(2)
                handle.setsampwidth(2)
                handle.setframerate(4)
                handle.writeframes(stereo.tobytes())

            pcm = load_wav_pcm16(path, target_rate=8, maximum_seconds=2)

        samples = np.frombuffer(pcm, dtype=np.int16)
        self.assertEqual(samples.size, 8)
        self.assertEqual(samples[0], 0)
        self.assertGreater(samples[-1], samples[0])


class SceneRunnerTests(unittest.TestCase):
    def test_runs_steps_in_order_and_reports_progress(self):
        scene = SceneLibrary.from_data(scene_data([
            {"action": "eyes", "level": 0.2},
            {"action": "jaw", "level": 0.5, "duration_ms": 10},
        ])).get("test_scene")
        actions = []
        progress = []
        runner = SceneRunner(
            executor=lambda step, _stop: actions.append(step.action) or False,
            progress=lambda _scene, index, step: progress.append((index, step.action)),
        )

        result = runner.run(scene, threading.Event())

        self.assertEqual(actions, [SceneAction.EYES, SceneAction.JAW])
        self.assertEqual(progress, [(1, SceneAction.EYES), (2, SceneAction.JAW)])
        self.assertEqual(result.outcome, "completed")
        self.assertEqual(result.completed_steps, 2)

    def test_foreground_interrupt_stops_before_the_next_step(self):
        scene = SceneLibrary.from_data(scene_data([
            {"action": "eyes", "level": 0.2},
            {"action": "jaw", "level": 0.5, "duration_ms": 10},
        ])).get("test_scene")
        interrupt = threading.Event()
        actions = []

        def execute(step, _stop):
            actions.append(step.action)
            interrupt.set()
            return True

        result = SceneRunner(execute).run(scene, interrupt)

        self.assertEqual(actions, [SceneAction.EYES])
        self.assertEqual(result.outcome, "interrupted")
        self.assertLess(result.completed_steps, result.total_steps)

    def test_maximum_runtime_is_a_hard_stop(self):
        scene = SceneLibrary.from_data(scene_data([
            {"action": "eyes", "level": 0.2},
            {"action": "jaw", "level": 0.5, "duration_ms": 10},
        ])).get("test_scene")
        clock = FakeClock()

        def execute(_step, _stop):
            clock.now = 2.0
            return False

        result = SceneRunner(
            execute,
            maximum_seconds=1.0,
            clock=clock,
        ).run(scene, threading.Event())

        self.assertEqual(result.outcome, "timeout")
        self.assertTrue(result.interrupted)

    def test_executor_failure_is_reported_without_escaping(self):
        scene = SceneLibrary.from_data(scene_data([
            {"action": "eyes", "level": 0.2},
        ])).get("test_scene")

        def fail(_step, _stop):
            raise RuntimeError("hardware unavailable")

        result = SceneRunner(fail).run(scene, threading.Event())

        self.assertEqual(result.outcome, "error")
        self.assertEqual(result.error, "hardware unavailable")


if __name__ == "__main__":
    unittest.main()
