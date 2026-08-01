import json
import tempfile
import unittest
from pathlib import Path

from holiday_skeleton.personality import (
    can_switch_personality,
    PersonalityConfigError,
    PersonalityLibrary,
)
from holiday_skeleton.scene import SceneLibrary


def pack(**overrides):
    data = {
        "display_name": "Test Ghost",
        "description": "A test personality.",
        "system_prompt": "Be spooky and brief.",
        "opening_lines": {
            "morning": ["Morning."],
            "afternoon": ["Afternoon."],
            "evening": ["Evening."],
            "night": ["Night."],
        },
        "goodbye_lines": ["Goodbye."],
        "idle_lines": ["Still waiting."],
        "fallback_line": "Try again.",
        "reply": {
            "memory_turns": 2,
            "context_tokens": 512,
            "maximum_tokens": 40,
            "temperature": 0.5,
            "repeat_penalty": 1.1,
            "phrase_minimum": 10,
            "phrase_soft": 30,
            "phrase_maximum": 60,
        },
        "voice": {"volume_multiplier": 0.8},
        "barge_in": {
            "stop_commands": ["stop"],
            "listen_commands": ["wait"],
            "wake_words": ["ghost"],
            "require_wake_word": True,
        },
        "default_scene": "awakening",
    }
    data.update(overrides)
    return data


class PersonalityLibraryTests(unittest.TestCase):
    def test_packaged_library_and_default_scenes_are_valid(self):
        root = Path(__file__).resolve().parents[1]
        library = PersonalityLibrary.load(root / "personalities.json")
        scenes = SceneLibrary.load(root / "scenes.json")

        self.assertEqual(
            library.names,
            ("pirate", "graveyard_host", "silent_watcher"),
        )
        self.assertEqual(library.default_name, "pirate")
        self.assertEqual(library.validate_scenes(scenes.names), ())

    def test_switching_is_allowed_only_between_foreground_activities(self):
        self.assertTrue(can_switch_personality("idle"))
        self.assertTrue(can_switch_personality("cooldown"))
        for state in ("starting", "greeting", "listening", "thinking", "speaking", "scene", "idle_life", "stopping"):
            self.assertFalse(can_switch_personality(state), state)

    def test_loads_pack_and_exposes_bounded_runtime_settings(self):
        library = PersonalityLibrary.from_data({
            "active": "ghost",
            "personalities": {"ghost": pack()},
        })

        ghost = library.select()
        self.assertEqual(ghost.name, "ghost")
        self.assertEqual(ghost.reply.memory_turns, 2)
        self.assertEqual(ghost.reply.phrase_maximum, 60)
        self.assertEqual(ghost.voice.volume_multiplier, 0.8)
        self.assertEqual(ghost.barge_in.wake_words, ("ghost",))
        self.assertEqual(ghost.default_scene, "awakening")

    def test_canned_lines_are_stable_and_unique(self):
        duplicate = pack(
            opening_lines={
                "morning": ["Same."],
                "afternoon": ["Same."],
                "evening": ["Evening."],
                "night": ["Night."],
            },
            goodbye_lines=["Same."],
        )
        personality = PersonalityLibrary.from_data({
            "personalities": {"ghost": duplicate}
        }).select()

        self.assertEqual(personality.canned_lines.count("Same."), 1)
        self.assertEqual(personality.canned_lines[0], "Same.")

    def test_explicit_selection_normalizes_name_and_rejects_unknown(self):
        library = PersonalityLibrary.from_data({
            "personalities": {"ghost": pack(), "watcher": pack(display_name="Watcher")}
        })

        self.assertEqual(library.select(" WATCHER ").name, "watcher")
        with self.assertRaisesRegex(PersonalityConfigError, "available: ghost, watcher"):
            library.select("missing")

    def test_rejects_unordered_phrase_boundaries(self):
        invalid = pack(reply={"phrase_minimum": 50, "phrase_soft": 20, "phrase_maximum": 40})
        with self.assertRaisesRegex(PersonalityConfigError, "minimum <= soft <= maximum"):
            PersonalityLibrary.from_data({"personalities": {"ghost": invalid}})

    def test_rejects_unknown_fields_instead_of_ignoring_typos(self):
        invalid = pack(reply={"memory_truns": 3})
        with self.assertRaisesRegex(PersonalityConfigError, "unknown fields: memory_truns"):
            PersonalityLibrary.from_data({"personalities": {"ghost": invalid}})

        with self.assertRaisesRegex(PersonalityConfigError, "version"):
            PersonalityLibrary.from_data({
                "version": 2,
                "personalities": {"ghost": pack()},
            })

    def test_rejects_missing_time_period_and_unsafe_scene(self):
        openings = pack()["opening_lines"].copy()
        openings.pop("night")
        with self.assertRaises(PersonalityConfigError):
            PersonalityLibrary.from_data({
                "personalities": {"ghost": pack(opening_lines=openings)}
            })
        with self.assertRaisesRegex(PersonalityConfigError, "safe scene name"):
            PersonalityLibrary.from_data({
                "personalities": {"ghost": pack(default_scene="../escape")}
            })

    def test_scene_validation_isolated_from_file_parsing(self):
        library = PersonalityLibrary.from_data({
            "personalities": {
                "ghost": pack(default_scene="awakening"),
                "watcher": pack(default_scene="missing"),
            }
        })

        self.assertEqual(
            library.validate_scenes(["awakening", "warning"]),
            ("watcher: unknown default scene 'missing'",),
        )

    def test_metadata_does_not_expose_full_prompts_or_lines(self):
        library = PersonalityLibrary.from_data({
            "personalities": {"ghost": pack()}
        })

        metadata = library.metadata()
        self.assertEqual(metadata["names"], ["ghost"])
        self.assertNotIn("system_prompt", metadata["personalities"]["ghost"])
        self.assertNotIn("opening_lines", metadata["personalities"]["ghost"])

    def test_load_reports_invalid_json_without_leaking_traceback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "personalities.json"
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaisesRegex(PersonalityConfigError, "cannot load personality file"):
                PersonalityLibrary.load(path)


if __name__ == "__main__":
    unittest.main()
