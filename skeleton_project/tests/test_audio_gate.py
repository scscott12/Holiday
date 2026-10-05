import unittest

import numpy as np

from holiday_skeleton.audio import (
    SpeechGate,
    output_stream_format,
    select_input_device,
)


class FakeOutputAudio:
    def __init__(self):
        self.checks = []

    def query_devices(self, device, kind):
        if device != "AB13X" or kind != "output":
            raise AssertionError((device, kind))
        return {
            "name": "AB13X USB Audio",
            "max_output_channels": 2,
            "default_samplerate": 48000.0,
        }

    def check_output_settings(self, **settings):
        self.checks.append(settings)
        if settings["samplerate"] != 48000 or settings["channels"] != 2:
            raise RuntimeError("unsupported hardware format")


class AudioDeviceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.devices = [
            {
                "name": "AB13X USB Audio: - (hw:1,0)",
                "max_input_channels": 2,
                "max_output_channels": 2,
            },
            {
                "name": "usb microphone: USB Audio (hw:2,0)",
                "max_input_channels": 1,
                "max_output_channels": 0,
            },
        ]

    def test_explicit_input_name_skips_headset_adapter_input(self):
        index, name = select_input_device(self.devices, "usb microphone")

        self.assertEqual(index, 1)
        self.assertEqual(name, "usb microphone: USB Audio (hw:2,0)")

    def test_invalid_explicit_input_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "was not found"):
            select_input_device(self.devices, "missing microphone")

    def test_output_uses_native_rate_and_stereo_when_mono_is_unsupported(self):
        audio = FakeOutputAudio()

        sample_rate, channels = output_stream_format(audio, "AB13X")

        self.assertEqual((sample_rate, channels), (48000, 2))
        self.assertEqual(
            [(item["samplerate"], item["channels"]) for item in audio.checks],
            [(48000, 1), (48000, 2)],
        )

    def test_invalid_output_rate_fails_before_opening_stream(self):
        audio = FakeOutputAudio()

        with self.assertRaisesRegex(ValueError, "must be an integer"):
            output_stream_format(audio, "AB13X", "not-a-rate")


class SpeechGateTests(unittest.TestCase):
    def make_gate(self):
        return SpeechGate(
            sample_rate=100,
            energy_threshold=10,
            preroll_seconds=0.3,
            minimum_voiced_seconds=0.2,
            end_silence_seconds=0.5,
        )

    def test_preroll_is_released_once_when_speech_starts(self):
        gate = self.make_gate()
        quiet = np.zeros(10, dtype=np.int16)
        voice = np.full(10, 100, dtype=np.int16)

        self.assertEqual(gate.process(quiet, 0.0).audio.size, 0)
        self.assertEqual(gate.process(voice, 0.1).audio.size, 0)
        started = gate.process(voice, 0.2)
        after = gate.process(voice, 0.3)

        self.assertTrue(started.speech_started)
        np.testing.assert_array_equal(
            started.audio,
            np.concatenate((quiet, voice, voice)),
        )
        np.testing.assert_array_equal(after.audio, voice)

    def test_silence_endpoint_starts_after_last_voiced_block(self):
        gate = self.make_gate()
        voice = np.full(10, 100, dtype=np.int16)
        quiet = np.zeros(10, dtype=np.int16)

        gate.process(voice, 1.0)
        gate.process(voice, 1.1)
        gate.process(quiet, 1.2)

        self.assertFalse(gate.silence_complete(1.59))
        self.assertTrue(gate.silence_complete(1.6))


if __name__ == "__main__":
    unittest.main()
