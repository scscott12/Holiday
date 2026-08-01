import unittest

import numpy as np

from holiday_skeleton.audio import SpeechGate


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

