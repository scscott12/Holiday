import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


class RuntimeImportSafetyTests(unittest.TestCase):
    def test_import_defers_pir_and_pca_construction_until_main_startup(self):
        calls = []

        class FakeMotionSensor:
            motion_detected = False

            def __init__(self, *_args, **_kwargs):
                calls.append("motion-sensor")

            def close(self):
                pass

        class FakePCA9685:
            def __init__(self, _i2c):
                calls.append("pca9685")
                self.channels = [types.SimpleNamespace(duty_cycle=0) for _ in range(16)]
                self.frequency = 0

        def fake_i2c(*_args, **_kwargs):
            calls.append("i2c")
            return object()

        def fake_servo(*_args, **_kwargs):
            calls.append("servo")
            return types.SimpleNamespace(fraction=None)

        gpiozero = types.ModuleType("gpiozero")
        gpiozero.MotionSensor = FakeMotionSensor
        board = types.ModuleType("board")
        board.SCL = object()
        board.SDA = object()
        busio = types.ModuleType("busio")
        busio.I2C = fake_i2c
        pca9685 = types.ModuleType("adafruit_pca9685")
        pca9685.PCA9685 = FakePCA9685
        servo = types.ModuleType("adafruit_motor.servo")
        servo.Servo = fake_servo
        motor = types.ModuleType("adafruit_motor")
        motor.__path__ = []
        motor.servo = servo
        fake_modules = {
            "gpiozero": gpiozero,
            "board": board,
            "busio": busio,
            "adafruit_pca9685": pca9685,
            "adafruit_motor": motor,
            "adafruit_motor.servo": servo,
        }
        runtime_path = Path(__file__).resolve().parents[1] / "skeleton_all_in_one_mqtt.py"

        with mock.patch.dict(sys.modules, fake_modules):
            namespace = runpy.run_path(
                str(runtime_path),
                run_name="holiday_skeleton_deployment_import_check",
            )

        self.assertEqual(calls, [])
        self.assertEqual(namespace["pir"].__class__.__name__, "_DummyPIR")
        self.assertIsNone(namespace["_pca"])
        self.assertIsNone(namespace["_eyes_ch"])
        self.assertIsNone(namespace["_jaw"])

        namespace["_initialize_hardware"]()

        self.assertEqual(calls, ["motion-sensor", "i2c", "pca9685", "servo"])


if __name__ == "__main__":
    unittest.main()
