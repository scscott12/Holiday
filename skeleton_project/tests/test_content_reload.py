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


class ContentReloadTests(unittest.TestCase):
    def _write(self, directory: Path, name: str, payload: object) -> Path:
        path = Path(directory) / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_valid_pack(self, directory: Path, name: str) -> Path:
        pack = {
            "display_name": "Test Pack",
            "system_prompt": "Test the content reload.",
            "opening_lines": {
                "morning": ["Hello, visitor."],
                "afternoon": ["Hello, visitor."],
                "evening": ["Hello, visitor."],
                "night": ["Hello, visitor."],
            },
            "goodbye_lines": ["Goodbye."],
            "idle_lines": ["Still here."],
            "fallback_line": "Repeat that.",
            "default_scene": "awakening",
        }
        return self._write(
            directory,
            name,
            {
                "version": 1,
                "active": "pirate",
                "personalities": {"pirate": pack},
            },
        )

    def _valid_scenes(self) -> dict:
        # The packaged personalities default to these scene names, so a valid
        # cross-reference needs each present.
        return {
            "version": 1,
            "scenes": {
                "awakening": {
                    "steps": [
                        {"action": "speak", "text": "Wake up."},
                    ],
                },
                "warning": {
                    "steps": [
                        {"action": "speak", "text": "Careful where you step."},
                    ],
                },
                "silent_scare": {
                    "steps": [
                        {"action": "speak", "text": "There you are."},
                    ],
                },
            },
        }

    def _prepare(self, **overrides) -> object:
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

    def test_loads_packaged_content(self):
        bundle = self._prepare()

        self.assertIsNotNone(bundle.personalities)
        self.assertIsNotNone(bundle.scenes)
        self.assertEqual(bundle.personalities.names, (
            "pirate",
            "graveyard_host",
            "silent_watcher",
        ))
        self.assertEqual(bundle.scenes.names, (
            "awakening",
            "warning",
            "silent_scare",
        ))
        self.assertEqual(bundle.active_personality.name, "pirate")
        # The active pack's canned lines and the scene's spoken lines both land
        # in the bundle's canned speech.
        self.assertIn(
            "Morning, matey. The deck is cold and the treasure is still asleep.",
            bundle.canned_lines,
        )
        self.assertIn(
            "Easy there, matey. Waking the dead before sunset is terribly rude.",
            bundle.canned_lines,
        )

    def test_active_personality_selection(self):
        bundle = self._prepare(current_personality="silent_watcher")

        self.assertEqual(bundle.active_personality.name, "silent_watcher")
        self.assertIn("There you are.", bundle.canned_lines)

    def test_disabled_sources_load_without_missing_files(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = str(Path(directory) / "missing.json")
            bundle = self._prepare(
                personalities_enabled=False,
                personalities_path=missing,
                scenes_enabled=False,
                scenes_path=missing,
                additional_canned_lines=("Hello visitor.", "Hello visitor.", " Bye. "),
            )

        self.assertIsNone(bundle.personalities)
        self.assertIsNone(bundle.active_personality)
        self.assertIsNone(bundle.scenes)
        self.assertEqual(dict(bundle.sound_cache), {})
        self.assertEqual(bundle.canned_lines, ("Hello visitor.", "Bye."))

    def test_malformed_json_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "malformed.json"
            path.write_text('{"unfinished":', encoding="utf-8")
            for label, override in (
                ("personality", {"personalities_path": str(path)}),
                ("scene", {"scenes_path": str(path)}),
            ):
                with self.subTest(label=label):
                    with self.assertRaisesRegex(
                        ContentReloadError,
                        f"cannot load {label} file",
                    ):
                        self._prepare(**override)

    def test_schema_violation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            personalities = self._write(
                directory,
                "personalities.json",
                {"version": 1, "active": "pirate", "personalities": {}},
            )
            with self.assertRaises(ContentReloadError):
                self._prepare(personalities_path=str(personalities))

            scenes = self._write(
                directory,
                "scenes.json",
                {
                    "version": 1,
                    "scenes": {
                        "empty": {"steps": []},
                    },
                },
            )
            with self.assertRaises(ContentReloadError):
                self._prepare(
                    personalities_enabled=False,
                    personalities_path="unused.json",
                    scenes_enabled=True,
                    scenes_path=str(scenes),
                    sound_directory=str(directory),
                )

    def test_missing_referenced_sound_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            scenes = self._write(
                root,
                "scenes.json",
                {
                    "version": 1,
                    "scenes": {
                        "knock": {
                            "steps": [
                                {"action": "sound", "file": "missing.wav"},
                            ],
                        },
                    },
                },
            )
            with self.assertRaisesRegex(
                ContentReloadError, "sound cue not found"
            ):
                prepare_content(
                    personalities_enabled=False,
                    personalities_path="unused.json",
                    requested_personality="legacy",
                    current_personality="legacy",
                    scenes_enabled=True,
                    scenes_path=str(scenes),
                    sound_directory=str(root),
                    sound_sample_rate=16000,
                    scene_maximum_seconds=30,
                    cache_sounds=True,
                )

    def test_failed_reload_retains_previous_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            personalities_path = self._write(
                Path(directory),
                "personalities.json",
                {
                    "version": 1,
                    "active": "pirate",
                    "personalities": {
                        "pirate": {
                            "display_name": "Pirate",
                            "system_prompt": "Pirate.",
                            "opening_lines": {
                                "morning": ["Greeting A."],
                                "afternoon": ["Greeting A."],
                                "evening": ["Greeting A."],
                                "night": ["Greeting A."],
                            },
                            "goodbye_lines": ["Goodbye."],
                            "idle_lines": ["Idle."],
                            "fallback_line": "Repeat.",
                            "default_scene": "awakening",
                        },
                    },
                },
            )
            scenes_path = self._write(Path(directory), "scenes.json", self._valid_scenes())
            options = dict(
                personalities_path=str(personalities_path),
                scenes_path=str(scenes_path),
                requested_personality="pirate",
                current_personality="pirate",
                sound_directory=str(directory),
            )

            original = self._prepare(**options)
            original_greeting = original.active_personality.opening_lines["morning"]

            with open(personalities_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
            data["personalities"]["pirate"]["opening_lines"]["morning"] = ["Greeting B."]
            personalities_path.write_text(json.dumps(data), encoding="utf-8")

            # A valid candidate commit replaces the previous bundle.
            reloaded = self._prepare(**options)
            self.assertEqual(
                reloaded.active_personality.opening_lines["morning"],
                ("Greeting B.",),
            )

            # A corrupted file fails to prepare: the previously committed bundle
            # is untouched and still holds the Greeting B. content.
            scenes_path.write_text("{", encoding="utf-8")
            with self.assertRaises(ContentReloadError):
                self._prepare(**options)

            self.assertEqual(
                reloaded.active_personality.opening_lines["morning"],
                ("Greeting B.",),
            )
            self.assertIn("Greeting B.", reloaded.canned_lines)
            self.assertNotIn(
                "Greeting A.",
                reloaded.active_personality.opening_lines["morning"],
            )

    def test_failed_reload_does_not_mutate_active_bundle(self):
        original = self._prepare()
        original_personality = original.personalities
        original_scenes = original.scenes
        original_lines = original.canned_lines

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ContentReloadError):
                self._prepare(
                    personalities_path=str(
                        Path(directory) / "does-not-exist.json"
                    ),
                )

        self.assertIs(original.personalities, original_personality)
        self.assertIs(original.scenes, original_scenes)
        self.assertEqual(original.canned_lines, original_lines)

    def test_interrupted_reload_does_not_commit(self):
        def interrupted() -> bool:
            return True

        with self.assertRaises(ContentReloadInterrupted):
            self._prepare(interrupted=interrupted)


if __name__ == "__main__":
    unittest.main()
