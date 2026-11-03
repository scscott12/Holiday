import configparser
import pathlib
import unittest


def _config_path() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent / ".flake8"


class TestLintConfig(unittest.TestCase):
    def test_flake8_config_exists(self):
        config_path = _config_path()
        self.assertTrue(config_path.is_file(), ".flake8 config file must exist")

    def test_max_line_length_is_120(self):
        config = configparser.ConfigParser()
        config.read(_config_path())
        value = config.get("flake8", "max-line-length")
        self.assertEqual(value, "120")

    def test_per_file_ignore_allows_f401_in_tests(self):
        config = configparser.ConfigParser()
        config.read(_config_path())
        per_file = config.get("flake8", "per-file-ignores", fallback="")
        self.assertIn("tests", per_file, "per-file-ignores must include tests")
        self.assertIn("F401", per_file, "per-file-ignores must allow F401 in tests")

    def test_flake8_runs_clean(self):
        import subprocess
        import sys

        project_dir = pathlib.Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-m", "flake8", "--jobs", "0", "holiday_skeleton", "scripts"],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"flake8 reported problems:\n{result.stdout}\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
