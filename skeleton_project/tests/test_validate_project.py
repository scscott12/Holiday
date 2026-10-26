"""Tests for scripts/validate_project.py.

The real project layout is checked through validate_project and main so the
package's own content, Home Assistant YAML, and discovery topics are exercised
without hardware. Failure paths use a temporary directory and assert main()'s
exit code, mirroring the module docstring: a broken or missing file is caught
by main() turning into a non-zero exit code rather than an uncaught error.
"""

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

# Import the script by its top-level name so it shares the same sys.path and
# sys.modules state the script's own module-level insert establishes; importing
# it as "scripts.validate_project" would load a second copy.
module = importlib.import_module("validate_project")
validate_project = module.validate_project
main = module.main
ProjectValidationError = module.ProjectValidationError
ContentReloadError = module.ContentReloadError

# Both of these are caught by main() and turn into a non-zero exit code.
VALIDATION_ERROR = (ProjectValidationError, ContentReloadError)


class ValidateProjectTests(unittest.TestCase):
    def test_real_project_layout_passes(self):
        # The exact counts are not asserted so the test keeps passing as
        # content is legitimately added; only that something is present.
        summary = validate_project(ROOT)
        self.assertGreater(summary["personalities"], 0)
        self.assertGreater(summary["scenes"], 0)
        self.assertGreater(summary["yaml_files"], 0)
        self.assertGreater(summary["discovery_topics"], 0)

    def test_main_returns_success_for_real_layout(self):
        # main() validates the real packaged content and returns its exit code.
        self.assertEqual(main(), 0)

    def test_missing_required_file_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ha").mkdir()
            (root / "ha" / "config.yaml").write_text(
                "device: skeleton\n", encoding="utf-8"
            )
            (root / "personalities.json").write_bytes(
                (ROOT / "personalities.json").read_bytes()
            )
            # scenes.json is deliberately absent: this is the missing required
            # file, distinct from the malformed-content cases below.
            with self.assertRaises(VALIDATION_ERROR):
                validate_project(root)

    def test_malformed_scenes_json_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ha").mkdir()
            (root / "ha" / "config.yaml").write_text(
                "device: skeleton\n", encoding="utf-8"
            )
            (root / "personalities.json").write_bytes(
                (ROOT / "personalities.json").read_bytes()
            )
            (root / "scenes.json").write_text(
                "{ this is not valid json", encoding="utf-8"
            )
            with self.assertRaises(VALIDATION_ERROR):
                validate_project(root)

    def test_malformed_personalities_json_fails_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ha").mkdir()
            (root / "ha" / "config.yaml").write_text(
                "device: skeleton\n", encoding="utf-8"
            )
            (root / "personalities.json").write_text(
                "{ this is not valid json", encoding="utf-8"
            )
            (root / "scenes.json").write_bytes(
                (ROOT / "scenes.json").read_bytes()
            )
            with self.assertRaises(VALIDATION_ERROR):
                validate_project(root)

    def test_missing_required_file_returns_non_zero_exit_code(self):
        # main() must turn a missing required file into a non-zero exit code
        # rather than letting it surface as an uncaught exception.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ha").mkdir()
            (root / "ha" / "config.yaml").write_text(
                "device: skeleton\n", encoding="utf-8"
            )
            (root / "personalities.json").write_bytes(
                (ROOT / "personalities.json").read_bytes()
            )
            # scenes.json is absent; main() should catch that and return 1.
            self.assertNotEqual(main(root), 0)

    def test_malformed_scenes_json_returns_non_zero_exit_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "ha").mkdir()
            (root / "ha" / "config.yaml").write_text(
                "device: skeleton\n", encoding="utf-8"
            )
            (root / "personalities.json").write_bytes(
                (ROOT / "personalities.json").read_bytes()
            )
            (root / "scenes.json").write_text(
                "{ this is not valid json", encoding="utf-8"
            )
            self.assertNotEqual(main(root), 0)


if __name__ == "__main__":
    unittest.main()
