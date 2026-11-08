import pathlib
import subprocess
import sys
import unittest


def _project_dir() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parent.parent


class TestLint(unittest.TestCase):
    def test_flake8_passes(self):
        project_dir = _project_dir()
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
