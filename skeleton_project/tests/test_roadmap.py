"""Roadmap status checks.

Asserts that ROADMAP.md no longer lists coverage reporting or static linting
under the 'Planned Next' section — they have been completed.
"""

import pathlib
import unittest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent.parent
ROADMAP = REPO_ROOT / "ROADMAP.md"


class TestRoadmap(unittest.TestCase):
    def test_coverage_not_planned_next(self):
        content = ROADMAP.read_text(encoding="utf-8")
        planned_section = self._extract_section(content, "## Planned Next")

        self.assertNotIn(
            "coverage",
            planned_section.lower(),
            "ROADMAP.md 'Planned Next' must not list coverage reporting as pending",
        )

    def test_linting_not_planned_next(self):
        content = ROADMAP.read_text(encoding="utf-8")
        planned_section = self._extract_section(content, "## Planned Next")

        self.assertNotIn(
            "linting",
            planned_section.lower(),
            "ROADMAP.md 'Planned Next' must not list static linting as pending",
        )

    def _extract_section(self, content: str, heading: str) -> str:
        """Return text between *heading* and the next '##' heading (or EOF)."""
        start = content.find(heading)
        self.assertNotEqual(start, -1, f"Roadmap must contain section '{heading}'")
        after_start = content[start + len(heading):]
        next_heading = after_start.find("## ")
        if next_heading == -1:
            return after_start
        return after_start[:next_heading]


if __name__ == "__main__":
    unittest.main()
