import unittest

from holiday_skeleton.personality import PersonalityConfigError, PersonalityLibrary
from holiday_skeleton.scene import SceneConfigError, SceneLibrary


def _scene_data(steps):
    return {
        "version": 1,
        "scenes": {
            "test_scene": {
                "description": "Test",
                "steps": steps,
            }
        },
    }


def _personality_data(voice):
    return {
        "version": 1,
        "active": "test",
        "personalities": {
            "test": {
                "display_name": "Test",
                "system_prompt": "Be helpful.",
                "opening_lines": {
                    "morning": ["Hi"],
                    "afternoon": ["Hi"],
                    "evening": ["Hi"],
                    "night": ["Hi"],
                },
                "goodbye_lines": {
                    "morning": ["Bye"],
                    "afternoon": ["Bye"],
                    "evening": ["Bye"],
                    "night": ["Bye"],
                },
                "idle_lines": ["Idle"],
                "fallback_line": "Say that again.",
                "reply": {},
                "voice": voice,
                "barge_in": {},
                "default_scene": "",
            }
        },
    }


class SceneSafetyTests(unittest.TestCase):
    def test_servo_angle_above_180_is_rejected(self):
        with self.assertRaises(SceneConfigError):
            SceneLibrary.from_data(_scene_data([
                {"action": "eyes", "level": 0.5, "angle": 181},
            ]))

    def test_servo_angle_at_180_is_accepted(self):
        library = SceneLibrary.from_data(_scene_data([
            {"action": "eyes", "level": 0.5, "angle": 180},
        ]))
        self.assertEqual(len(library), 1)

    def test_jaw_servo_angle_above_180_is_rejected(self):
        with self.assertRaises(SceneConfigError):
            SceneLibrary.from_data(_scene_data([
                {"action": "jaw", "level": 0.5, "duration": 1.0, "angle": 200},
            ]))

    def test_servo_angle_negative_is_rejected(self):
        with self.assertRaises(SceneConfigError):
            SceneLibrary.from_data(_scene_data([
                {"action": "eyes", "level": 0.5, "angle": -1},
            ]))

    def test_sound_volume_above_2_is_rejected(self):
        with self.assertRaises(SceneConfigError):
            SceneLibrary.from_data(_scene_data([
                {"action": "sound", "file": "cue.wav", "volume": 2.5},
            ]))


class PersonalitySafetyTests(unittest.TestCase):
    def test_volume_above_100_is_rejected(self):
        with self.assertRaises(PersonalityConfigError):
            PersonalityLibrary.from_data(_personality_data({"volume": 101}))

    def test_volume_at_100_is_accepted(self):
        library = PersonalityLibrary.from_data(_personality_data({"volume": 100}))
        self.assertEqual(len(library), 1)

    def test_volume_multiplier_above_2_is_rejected(self):
        with self.assertRaises(PersonalityConfigError):
            PersonalityLibrary.from_data(_personality_data({"volume_multiplier": 2.5}))

    def test_valid_volume_is_accepted(self):
        library = PersonalityLibrary.from_data(_personality_data({"volume": 50}))
        self.assertEqual(len(library), 1)


if __name__ == "__main__":
    unittest.main()
