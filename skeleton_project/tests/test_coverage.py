"""Coverage threshold check.

Run the hardware-free tests under ``coverage`` and assert that the measured
coverage of ``holiday_skeleton`` is at least 80%. This module and the
repository-wide lint/config checks are left out of the measured run: running
this module again would recurse, and the others do not exercise the package.
"""

import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parent.parent
TESTS_DIR = pathlib.Path(__file__).resolve().parent
COVERAGE_THRESHOLD = 80.0
SKIPPED_MODULES = {"test_coverage", "test_lint", "test_lint_config", "test_ci_config"}


def _measured_modules():
    return sorted(
        f"tests.{path.stem}"
        for path in TESTS_DIR.glob("test_*.py")
        if path.stem not in SKIPPED_MODULES
    )


class CoverageThresholdTests(unittest.TestCase):
    def _coverage(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "coverage", *args],
            cwd=PROJECT_DIR,
            capture_output=True,
            text=True,
            timeout=600,
        )

    def test_coverage_meets_threshold(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_file = str(pathlib.Path(tmp) / "coverage-data")
            json_file = str(pathlib.Path(tmp) / "coverage.json")

            run = self._coverage(
                "run", f"--data-file={data_file}", "--source", "holiday_skeleton",
                "-m", "unittest", *_measured_modules(),
            )
            self.assertEqual(
                run.returncode, 0,
                f"running tests under coverage failed:\n{run.stderr[-2000:]}",
            )

            report = self._coverage(
                "json", f"--data-file={data_file}", "-o", json_file
            )
            self.assertEqual(
                report.returncode, 0,
                f"generating the coverage report failed:\n{report.stderr}",
            )
            totals = json.loads(pathlib.Path(json_file).read_text("utf-8"))["totals"]

        # An empty measurement must not pass the gate.
        self.assertGreater(totals["num_statements"], 0, "coverage measured nothing")
        percent = totals["percent_covered"]
        self.assertGreaterEqual(
            percent, COVERAGE_THRESHOLD,
            f"coverage {percent:.1f}% is below the {COVERAGE_THRESHOLD:.0f}% threshold",
        )


if __name__ == "__main__":
    unittest.main()
