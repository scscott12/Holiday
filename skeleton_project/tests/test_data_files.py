"""Schema checks for the packaged JSON data files.

These tests load the shipped ``scenes.json``, ``personalities.json`` and
``prompts.json.example`` and assert the files are valid JSON, expose the
expected top-level structure, keep their names unique, and that every
cross-reference (a personality's default scene, the active personality)
resolves.  Where a runtime loader already enforces the schema, the real
packaged files must pass it; ``prompts.json.example`` has no loader in
``holiday_skeleton`` so its shape is validated against the keys the
runtime reads from it.

Duplicate-key detection uses ``object_pairs_hook`` on the raw text so a
duplicated scene or personality name cannot be silently overwritten by
``json.load`` before the test sees it.  ``object_pairs_hook`` on the top
level only sees the top-level keys, so the nested ``scenes`` /
``personalities`` containers are re-parsed from their raw substring.
"""

import json
import unittest
from collections import Counter
from pathlib import Path

from holiday_skeleton.personality import PersonalityLibrary
from holiday_skeleton.scene import SceneLibrary

ROOT = Path(__file__).resolve().parents[1]

# Opening-line periods the runtime and personality loader both expect.
_PERIODS = ("morning", "afternoon", "evening", "night")

# Prompts keys the runtime reads via _try_load_prompts in the all-in-one script.
_PROMPT_KEYS = (
    "SYSTEM_PROMPT",
    "MORNING_LINES",
    "AFTERNOON_LINES",
    "EVENING_LINES",
    "NIGHT_LINES",
    "GOODBYE_LINES",
    "IDLE_LINES",
)
_LIST_KEYS = _PROMPT_KEYS[1:]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load(path: Path) -> dict:
    return json.loads(_read(path))


def _inner_object_raw(path: Path, container_key: str) -> str:
    """Return the raw JSON text of a named inner object.

    ``json.load`` with ``object_pairs_hook=list`` keeps every pair, but
    on the top level it only sees the top-level keys.  A duplicate
    *scene* or *personality* name lives inside the ``scenes`` /
    ``personalities`` object, so we extract that object's raw substring
    and re-parse it with ``object_pairs_hook=list`` to see its pairs
    before the parser collapses any duplicates.  The substring is found
    by scanning brace depth rather than a greedy regex so a nested
    ``}`` inside the container does not end the match early.
    """
    raw = _read(path)
    key = f'"{container_key}"'
    index = raw.find(key)
    assert index != -1, f"could not locate {container_key} in {path.name}"
    brace = raw.find("{", index + len(key))
    assert brace != -1, f"could not locate {container_key} value in {path.name}"
    depth = 0
    in_string = False
    escaped = False
    for position in range(brace, len(raw)):
        char = raw[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[brace:position + 1]
    raise AssertionError(f"unbalanced braces in {container_key} of {path.name}")


def _inner_pairs(path: Path, container_key: str):
    """Return the ``(key, value)`` pairs for a named inner container."""
    return json.loads(_inner_object_raw(path, container_key), object_pairs_hook=list)


def _duplicate_names(pairs) -> list:
    """Return the names that appear more than once in ``pairs``."""
    return sorted(name for name, count in Counter(name for name, _ in pairs).items() if count > 1)


def _top_level_pairs(path: Path):
    """Return the top-level ``(key, value)`` pairs of a JSON file."""
    return json.loads(_read(path), object_pairs_hook=list)


class TestDataFiles(unittest.TestCase):
    """Schema and cross-reference checks for the three packaged data files."""

    # -- scenes.json -------------------------------------------------------

    def test_scenes_valid_json_and_top_level(self):
        data = _load(ROOT / "scenes.json")
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), {"version", "scenes"})
        self.assertEqual(data["version"], 1)
        self.assertIsInstance(data["scenes"], dict)
        self.assertTrue(data["scenes"], "scenes must not be empty")

    def test_scenes_top_level_keys_unique(self):
        dupes = _duplicate_names(_top_level_pairs(ROOT / "scenes.json"))
        self.assertEqual(dupes, [], f"duplicate top-level keys in scenes.json: {dupes}")

    def test_scenes_scene_names_unique(self):
        dupes = _duplicate_names(_inner_pairs(ROOT / "scenes.json", "scenes"))
        self.assertEqual(dupes, [], f"duplicate scene names: {dupes}")

    def test_scenes_each_scene_has_expected_keys_and_types(self):
        data = _load(ROOT / "scenes.json")
        for name, scene in data["scenes"].items():
            with self.subTest(scene=name):
                self.assertIsInstance(scene, dict)
                self.assertEqual(set(scene), {"description", "steps"})
                self.assertIsInstance(scene["description"], str)
                self.assertIsInstance(scene["steps"], list)
                self.assertTrue(scene["steps"], "steps must not be empty")
                for step in scene["steps"]:
                    self.assertIsInstance(step, dict)
                    self.assertIn("action", step)

    def test_scenes_loader_accepts_packaged_file(self):
        library = SceneLibrary.load(ROOT / "scenes.json")
        self.assertTrue(len(library) > 0, "loader returned no scenes")
        # The loader enforces non-empty, well-typed steps while loading.
        for name in library.names:
            self.assertIsNotNone(library.get(name))

    # -- personalities.json -----------------------------------------------

    def test_personalities_valid_json_and_top_level(self):
        data = _load(ROOT / "personalities.json")
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), {"version", "active", "personalities"})
        self.assertEqual(data["version"], 1)
        self.assertIsInstance(data["active"], str)
        self.assertIsInstance(data["personalities"], dict)
        self.assertTrue(data["personalities"], "personalities must not be empty")

    def test_personalities_top_level_keys_unique(self):
        dupes = _duplicate_names(_top_level_pairs(ROOT / "personalities.json"))
        self.assertEqual(dupes, [], f"duplicate top-level keys in personalities.json: {dupes}")

    def test_personalities_pack_names_unique(self):
        dupes = _duplicate_names(_inner_pairs(ROOT / "personalities.json", "personalities"))
        self.assertEqual(dupes, [], f"duplicate personality names: {dupes}")

    def test_personalities_required_fields_and_types(self):
        data = _load(ROOT / "personalities.json")
        for name, pack in data["personalities"].items():
            with self.subTest(personality=name):
                self.assertIsInstance(pack, dict)
                for field in (
                    "display_name", "description", "system_prompt",
                    "opening_lines", "goodbye_lines", "idle_lines",
                    "fallback_line", "reply", "voice", "barge_in",
                    "default_scene",
                ):
                    self.assertIn(field, pack)
                self.assertIsInstance(pack["display_name"], str)
                self.assertIsInstance(pack["description"], str)
                self.assertIsInstance(pack["system_prompt"], str)
                self.assertIsInstance(pack["fallback_line"], str)
                self.assertIsInstance(pack["default_scene"], str)
                self.assertIsInstance(pack["opening_lines"], dict)
                self.assertEqual(set(pack["opening_lines"]), set(_PERIODS))
                for period in _PERIODS:
                    lines = pack["opening_lines"][period]
                    self.assertIsInstance(lines, list)
                    self.assertTrue(lines, f"{period} opening lines empty")
                    self.assertTrue(
                        all(isinstance(line, str) for line in lines),
                        f"{period} opening lines must be strings",
                    )
                self.assertIsInstance(pack["reply"], dict)
                self.assertIsInstance(pack["voice"], dict)
                self.assertIsInstance(pack["barge_in"], dict)

    def test_personalities_loader_accepts_packaged_file(self):
        library = PersonalityLibrary.load(ROOT / "personalities.json")
        self.assertTrue(len(library) > 0, "loader returned no personalities")
        for name in library.names:
            self.assertIsNotNone(library.get(name))

    def test_personalities_active_personality_resolves(self):
        data = _load(ROOT / "personalities.json")
        active = data["active"]
        self.assertIn(active, data["personalities"])
        library = PersonalityLibrary.load(ROOT / "personalities.json")
        self.assertIsNotNone(library.get(active))
        self.assertEqual(library.default_name, active)

    def test_personalities_default_scenes_resolve(self):
        data = _load(ROOT / "personalities.json")
        scene_names = set(_load(ROOT / "scenes.json")["scenes"])
        for name, pack in data["personalities"].items():
            with self.subTest(personality=name):
                self.assertIn(
                    pack["default_scene"],
                    scene_names,
                    f"{name}.default_scene is not in scenes.json",
                )
        # The loader's own cross-reference check is the source of truth.
        library = PersonalityLibrary.load(ROOT / "personalities.json")
        self.assertEqual(library.validate_scenes(scene_names), ())

    # -- prompts.json.example ----------------------------------------------

    def test_prompts_valid_json_and_expected_keys(self):
        data = _load(ROOT / "prompts.json.example")
        self.assertIsInstance(data, dict)
        self.assertEqual(set(data), set(_PROMPT_KEYS))

    def test_prompts_system_prompt_is_text(self):
        data = _load(ROOT / "prompts.json.example")
        self.assertIsInstance(data["SYSTEM_PROMPT"], str)
        self.assertTrue(data["SYSTEM_PROMPT"].strip())

    def test_prompts_line_groups_are_non_empty_string_lists(self):
        data = _load(ROOT / "prompts.json.example")
        for key in _LIST_KEYS:
            with self.subTest(key=key):
                lines = data[key]
                self.assertIsInstance(lines, list)
                self.assertTrue(lines, f"{key} must not be empty")
                self.assertTrue(
                    all(isinstance(line, str) for line in lines),
                    f"{key} must contain strings",
                )

    def test_prompts_line_groups_have_unique_non_empty_entries(self):
        data = _load(ROOT / "prompts.json.example")
        for key in _LIST_KEYS:
            with self.subTest(key=key):
                entries = [line.strip() for line in data[key]]
                self.assertTrue(all(entries), f"{key} has empty entries")
                self.assertEqual(_duplicate_names(Counter(entries).items()), [])


if __name__ == "__main__":
    unittest.main()