import json
import tempfile
import unittest
import wave
from pathlib import Path

from holiday_skeleton.content import (
    ContentReloadError,
    ContentReloadInterrupted,
    prepare_content,
)


ROOT = Path(__file__).resolve().parents[1]


class ContentPreparationTests(unittest.TestCase):
    def _prepare(self, **overrides):
        options = dict(
            personalities_enabled=True,
            personalities_path=str(ROOT / "personalities.json"),
            requested_personality="pirate",
            current_personality="pirate",
            scenes_enabled=True,
            scenes_path=str(ROOT / "scenes.json"),
            sound_directory=str(ROOT / "sounds"),
            sound_sample_rate=16000,
            scene_maximum_seconds=30,
            cache_sounds=False,
        )
        options.update(overrides)
        return prepare_content(**options)

    def test_missing_content_files_raise_reload_error(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.json")
            for source, label in (("personalities", "personality"), ("scenes", "scene")):
                with self.subTest(source=source):
                    with self.assertRaisesRegex(
                        ContentReloadError, f"cannot inspect {label} file"
                    ):
                        self._prepare(**{f"{source}_path": missing})

    def test_malformed_json_raises_reload_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            path.write_text('{"unfinished":', encoding="utf-8")
            label_map = {"personalities": "personality", "scenes": "scene"}
            for source in ("personalities", "scenes"):
                with self.subTest(source=source):
                    with self.assertRaisesRegex(
                        ContentReloadError,
                        f"cannot load {label_map[source]} file",
                    ):
                        self._prepare(**{f"{source}_path": str(path)})

    def test_disabled_sources_ignore_missing_files_and_normalize_extra_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.json")
            bundle = self._prepare(
                personalities_enabled=False,
                personalities_path=missing,
                scenes_enabled=False,
                scenes_path=missing,
                additional_canned_lines=("  Hello\n visitor. ", "", "Hello visitor.", " Bye. "),
            )

        self.assertIsNone(bundle.personalities)
        self.assertIsNone(bundle.active_personality)
        self.assertIsNone(bundle.scenes)
        self.assertEqual(dict(bundle.sound_cache), {})
        self.assertEqual(bundle.canned_lines, ("Hello visitor.", "Bye."))

    def test_reload_reads_edits_without_mutating_previous_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            personalities_path = Path(directory) / "personalities.json"
            scenes_path = Path(directory) / "scenes.json"
            personalities = json.loads((ROOT / "personalities.json").read_text(encoding="utf-8"))
            scenes = json.loads((ROOT / "scenes.json").read_text(encoding="utf-8"))
            personalities_path.write_text(json.dumps(personalities), encoding="utf-8")
            scenes_path.write_text(json.dumps(scenes), encoding="utf-8")
            paths = dict(personalities_path=str(personalities_path), scenes_path=str(scenes_path))
            original = self._prepare(**paths)
            original_lines = original.canned_lines
            old_greetings = original.active_personality.opening_lines["morning"]

            personalities["personalities"]["pirate"]["opening_lines"]["morning"] = ["New greeting."]
            scenes["scenes"]["awakening"]["steps"] = [
                {"action": "speak", "text": "New scene response."},
            ]
            personalities_path.write_text(json.dumps(personalities), encoding="utf-8")
            scenes_path.write_text(json.dumps(scenes), encoding="utf-8")
            reloaded = self._prepare(**paths)

            self.assertIsNot(reloaded.personalities, original.personalities)
            self.assertIsNot(reloaded.scenes, original.scenes)
            self.assertEqual(reloaded.active_personality.name, "pirate")
            self.assertEqual(reloaded.active_personality.opening_lines["morning"], ("New greeting.",))
            self.assertIn("New scene response.", reloaded.canned_lines)
            self.assertNotIn(old_greetings[0], reloaded.canned_lines)
            self.assertEqual(original.active_personality.opening_lines["morning"], old_greetings)
            self.assertEqual(original.canned_lines, original_lines)
            self.assertNotIn("New greeting.", original.canned_lines)
            self.assertNotIn("New scene response.", original.canned_lines)

            scenes_path.write_text("{", encoding="utf-8")
            with self.assertRaises(ContentReloadError):
                self._prepare(**paths)
            self.assertEqual(reloaded.active_personality.opening_lines["morning"], ("New greeting.",))
            self.assertIn("New scene response.", reloaded.canned_lines)

    def test_packaged_content_prepares_current_pack_and_scene_lines(self):
        bundle = prepare_content(
            personalities_enabled=True,
            personalities_path=str(ROOT / "personalities.json"),
            requested_personality="pirate",
            current_personality="silent_watcher",
            scenes_enabled=True,
            scenes_path=str(ROOT / "scenes.json"),
            sound_directory=str(ROOT / "sounds"),
            sound_sample_rate=16000,
            scene_maximum_seconds=30,
            cache_sounds=False,
            additional_canned_lines=("Systems awake and ready.",),
        )

        self.assertEqual(bundle.active_personality.name, "silent_watcher")
        self.assertEqual(bundle.personalities.names, (
            "pirate", "graveyard_host", "silent_watcher",
        ))
        self.assertEqual(bundle.scenes.names, (
            "awakening", "warning", "silent_scare",
        ))
        self.assertIn("There you are.", bundle.canned_lines)
        self.assertIn(
            "Easy there, matey. Waking the dead before sunset is terribly rude.",
            bundle.canned_lines,
        )
        self.assertIn("Systems awake and ready.", bundle.canned_lines)

    def test_unknown_default_scene_rejects_complete_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "scenes.json"
            path.write_text(json.dumps({
                "version": 1,
                "scenes": {
                    "different": {
                        "steps": [{"action": "pause", "duration_ms": 10}],
                    },
                },
            }), encoding="utf-8")

            with self.assertRaisesRegex(ContentReloadError, "unknown default scene"):
                prepare_content(
                    personalities_enabled=True,
                    personalities_path=str(ROOT / "personalities.json"),
                    requested_personality="pirate",
                    current_personality="pirate",
                    scenes_enabled=True,
                    scenes_path=str(path),
                    sound_directory=directory,
                    sound_sample_rate=16000,
                    scene_maximum_seconds=30,
                    cache_sounds=False,
                )

    def test_current_personality_cannot_disappear_during_reload(self):
        with self.assertRaisesRegex(ContentReloadError, "unknown personality"):
            prepare_content(
                personalities_enabled=True,
                personalities_path=str(ROOT / "personalities.json"),
                requested_personality="pirate",
                current_personality="removed_pack",
                scenes_enabled=True,
                scenes_path=str(ROOT / "scenes.json"),
                sound_directory=str(ROOT / "sounds"),
                sound_sample_rate=16000,
                scene_maximum_seconds=30,
                cache_sounds=False,
            )

    def test_sound_is_validated_and_preloaded_before_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sound = root / "knock.wav"
            with wave.open(str(sound), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes((b"\x00\x00\x10\x00") * 80)
            scenes = root / "scenes.json"
            scenes.write_text(json.dumps({
                "version": 1,
                "scenes": {
                    "knock": {
                        "steps": [{"action": "sound", "file": "knock.wav"}],
                    },
                },
            }), encoding="utf-8")

            bundle = prepare_content(
                personalities_enabled=False,
                personalities_path="unused.json",
                requested_personality="",
                current_personality="legacy",
                scenes_enabled=True,
                scenes_path=str(scenes),
                sound_directory=str(root),
                sound_sample_rate=16000,
                scene_maximum_seconds=30,
                cache_sounds=True,
            )

        self.assertIn("knock.wav", bundle.sound_cache)
        self.assertGreater(len(bundle.sound_cache["knock.wav"]), 0)

    def test_decoded_sound_budget_prevents_pi_memory_spike(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sound = root / "knock.wav"
            with wave.open(str(sound), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                handle.writeframes((b"\x00\x00\x10\x00") * 80)
            scenes = root / "scenes.json"
            scenes.write_text(json.dumps({
                "scenes": {
                    "knock": {
                        "steps": [{"action": "sound", "file": "knock.wav"}],
                    },
                },
            }), encoding="utf-8")

            with self.assertRaisesRegex(ContentReloadError, "sound.*limit"):
                prepare_content(
                    personalities_enabled=False,
                    personalities_path="unused.json",
                    requested_personality="",
                    current_personality="legacy",
                    scenes_enabled=True,
                    scenes_path=str(scenes),
                    sound_directory=str(root),
                    sound_sample_rate=16000,
                    scene_maximum_seconds=30,
                    cache_sounds=True,
                    maximum_sound_bytes=64,
                )

    def test_canned_speech_budget_bounds_reload_work(self):
        with self.assertRaisesRegex(ContentReloadError, "cannot cache more than 1"):
            prepare_content(
                personalities_enabled=False,
                personalities_path="unused.json",
                requested_personality="",
                current_personality="legacy",
                scenes_enabled=True,
                scenes_path=str(ROOT / "scenes.json"),
                sound_directory=str(ROOT / "sounds"),
                sound_sample_rate=16000,
                scene_maximum_seconds=30,
                cache_sounds=False,
                maximum_canned_lines=1,
            )

    def test_foreground_checkpoint_interrupts_preparation(self):
        checks = 0

        def interrupted():
            nonlocal checks
            checks += 1
            return checks >= 2

        with self.assertRaises(ContentReloadInterrupted):
            prepare_content(
                personalities_enabled=True,
                personalities_path=str(ROOT / "personalities.json"),
                requested_personality="pirate",
                current_personality="pirate",
                scenes_enabled=True,
                scenes_path=str(ROOT / "scenes.json"),
                sound_directory=str(ROOT / "sounds"),
                sound_sample_rate=16000,
                scene_maximum_seconds=30,
                cache_sounds=False,
                interrupted=interrupted,
            )


if __name__ == "__main__":
    unittest.main()
