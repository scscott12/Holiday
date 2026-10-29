import unittest


class TestCiConfig(unittest.TestCase):
    def test_coverage_in_requirements_ci(self):
        import pathlib

        requirements = pathlib.Path(__file__).resolve().parent.parent / "requirements-ci.txt"
        content = requirements.read_text(encoding="utf-8")
        self.assertIn("coverage", content, "coverage must be listed in requirements-ci.txt")


if __name__ == "__main__":
    unittest.main()
