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

    def test_idle_gate_blocks_silence_and_noise_at_or_below_threshold(self):
        for amplitude in (0, 9, 10, -10):
            with self.subTest(amplitude=amplitude):
                gate = self.make_gate()
                samples = np.full(10, amplitude, dtype=np.int16)

                self.assertFalse(gate.speaking)
                for now in (0.0, 0.1, 0.2, 0.3):
                    result = gate.process(samples, now)
                    self.assertEqual(result.audio.size, 0)
                    self.assertFalse(result.speech_started)
                    self.assertFalse(gate.speaking)
                self.assertFalse(gate.silence_complete(100.0))

    def test_mean_absolute_energy_above_threshold_activates_gate(self):
        gate = self.make_gate()
        # A zero signed mean is still voiced when mean absolute energy is 11.
        voice = np.array([11, -11] * 5, dtype=np.int16)

        pending = gate.process(voice, 0.0)
        self.assertEqual(pending.audio.size, 0)
        self.assertFalse(pending.speech_started)
        self.assertFalse(gate.speaking)

        started = gate.process(voice, 0.1)
        self.assertTrue(gate.speaking)
        self.assertTrue(started.speech_started)
        np.testing.assert_array_equal(started.audio, np.tile(voice, 2))

    def test_quiet_block_cancels_brief_noise_burst_progress(self):
        gate = self.make_gate()
        voice = np.full(10, 100, dtype=np.int16)
        # One loud sample does not make the block's mean energy exceed 10.
        noise = np.array([100] + [0] * 9, dtype=np.int16)

        for now, samples in enumerate((voice, noise, voice)):
            result = gate.process(samples, now * 0.1)
            self.assertEqual(result.audio.size, 0)
            self.assertFalse(result.speech_started)
            self.assertFalse(gate.speaking)

        started = gate.process(voice, 0.3)
        self.assertTrue(started.speech_started)
        self.assertTrue(gate.speaking)
        np.testing.assert_array_equal(
            started.audio, np.concatenate((noise, voice, voice))
        )

    def test_active_gate_passes_quiet_audio_and_refreshes_silence_timer(self):
        gate = self.make_gate()
        voice = np.full(10, 100, dtype=np.int16)
        gate.process(voice, 0.0)
        gate.process(voice, 0.1)

        for now, amplitude in ((0.2, 0), (0.3, 10), (0.5, -11)):
            samples = np.full(10, amplitude, dtype=np.int16)
            result = gate.process(samples, now)
            np.testing.assert_array_equal(result.audio, samples)
            self.assertFalse(result.speech_started)
            self.assertTrue(gate.speaking)

        self.assertFalse(gate.silence_complete(0.99))
        self.assertTrue(gate.silence_complete(1.0))
        # The caller resets the gate after handling the silence endpoint.
        gate.reset()
        result = gate.process(voice, 1.1)
        self.assertEqual(result.audio.size, 0)
        self.assertFalse(result.speech_started)
        self.assertFalse(gate.speaking)

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

    def test_reset_after_interrupted_speech_restores_idle_gate(self):
        gate = self.make_gate()
        voice = np.full(10, 100, dtype=np.int16)
        quiet = np.zeros(10, dtype=np.int16)

        gate.process(voice, 0.0)
        self.assertTrue(gate.process(voice, 0.1).speech_started)
        self.assertTrue(gate.speaking)

        gate.reset()

        self.assertFalse(gate.speaking)
        self.assertFalse(gate.silence_complete(100.0))
        # Quiet audio is held back again rather than passed through.
        self.assertEqual(gate.process(quiet, 5.0).audio.size, 0)

    def test_reset_discards_stale_preroll_and_voiced_progress(self):
        gate = self.make_gate()
        old = np.full(10, 50, dtype=np.int16)
        voice = np.full(10, 100, dtype=np.int16)

        gate.process(old, 0.0)  # voiced 0.1s of the 0.2s required
        gate.reset()

        self.assertEqual(gate.process(voice, 1.0).audio.size, 0)
        started = gate.process(voice, 1.1)
        self.assertTrue(started.speech_started)
        np.testing.assert_array_equal(
            started.audio, np.concatenate((voice, voice))
        )

    def test_gate_can_restart_after_reset_and_endpoint_again(self):
        gate = self.make_gate()
        voice = np.full(10, 100, dtype=np.int16)

        gate.process(voice, 0.0)
        gate.process(voice, 0.1)
        self.assertTrue(gate.silence_complete(0.6))
        gate.reset()
        gate.process(voice, 2.0)
        self.assertTrue(gate.process(voice, 2.1).speech_started)

        self.assertFalse(gate.silence_complete(2.5))
        self.assertTrue(gate.silence_complete(2.6))


if __name__ == "__main__":
    unittest.main()
