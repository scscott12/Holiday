import pathlib
import unittest


class RuntimeRequirementsTests(unittest.TestCase):
    def test_raspberry_pi_gpio_backends_are_declared(self):
        requirements = (
            pathlib.Path(__file__).resolve().parent.parent / "requirements.txt"
        ).read_text(encoding="utf-8")

        self.assertIn("Adafruit-Blinka-Raspberry-Pi-PWM>=1.0.2", requirements)
        self.assertIn("gpiod>=2.2.2,<3", requirements)


if __name__ == "__main__":
    unittest.main()
