import threading
import unittest
from types import SimpleNamespace

import numpy as np

from holiday_skeleton.speech import (
    PiperSpeechEngine,
    jaw_envelope,
    scale_pcm16,
    split_pcm16_frames,
)


class FakeChunk:
    sample_rate = 1000
    sample_width = 2
    sample_channels = 1

    def __init__(self, samples):
        self.audio_int16_bytes = np.asarray(samples, dtype=np.int16).tobytes()


class FakeVoice:
    config = SimpleNamespace(sample_rate=1000)

    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        yield from self.chunks


class FakeStream:
    def __init__(self):
        self.writes = []
        self.started = 0
        self.stopped = 0
        self.aborted = 0
        self.closed = 0

    def start(self):
        self.started += 1

    def write(self, data):
        self.writes.append(bytes(data))

    def stop(self):
        self.stopped += 1

    def abort(self):
        self.aborted += 1

    def close(self):
        self.closed += 1


class FakeAudio:
    def __init__(self):
        self.stream = FakeStream()
        self.created = []

    def RawOutputStream(self, **kwargs):
        self.created.append(kwargs)
        return self.stream


class SpeechHelpersTests(unittest.TestCase):
    def test_frames_stay_sample_aligned(self):
        samples = np.arange(25, dtype=np.int16)
        frames = split_pcm16_frames(samples.tobytes(), 1000, frame_ms=10)

        self.assertEqual([len(frame) for frame in frames], [20, 20, 10])
        np.testing.assert_array_equal(
            np.frombuffer(b"".join(frames), dtype=np.int16), samples
        )

    def test_volume_scaling_clips_instead_of_wrapping(self):
        frame = np.array([20000, -20000], dtype=np.int16).tobytes()
        scaled = np.frombuffer(scale_pcm16(frame, 2.0), dtype=np.int16)
        np.testing.assert_array_equal(scaled, np.array([32767, -32768], dtype=np.int16))

    def test_jaw_envelope_tracks_loudness(self):
        quiet = np.full(10, 100, dtype=np.int16).tobytes()
        loud = np.full(10, 1000, dtype=np.int16).tobytes()
        levels = jaw_envelope([quiet, loud])

        self.assertLess(levels[0], levels[1])
        self.assertGreater(levels[1], 0.5)


class PiperSpeechEngineTests(unittest.TestCase):
    def make_engine(self, chunks):
        self.voice = FakeVoice(chunks)
        self.audio = FakeAudio()
        self.jaw = []
        engine = PiperSpeechEngine(
            voice=self.voice,
            audio_module=self.audio,
            jaw_set=self.jaw.append,
            volume_getter=lambda: 1.0,
            rest_fraction=0.25,
            maximum_fraction=1.0,
            frame_ms=10,
        )
        return engine

    def test_reuses_voice_and_output_stream_across_utterances(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])

        first = engine.speak("Ahoy")
        second = engine.speak("Matey")

        self.assertEqual(self.voice.calls, ["Ahoy", "Matey"])
        self.assertEqual(len(self.audio.created), 1)
        self.assertEqual(first.frames_written, 2)
        self.assertEqual(second.frames_written, 2)
        self.assertEqual(self.audio.stream.started, 3)
        self.assertEqual(self.jaw[-1], 0.25)

    def test_warm_up_materializes_audio_without_playing_it(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])

        elapsed = engine.warm_up("Ready")

        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(self.voice.calls, ["Ready"])
        self.assertEqual(self.audio.stream.writes, [])

    def test_stop_event_interrupts_between_small_pcm_frames(self):
        engine = self.make_engine([FakeChunk(np.full(30, 1000))])
        stop_event = threading.Event()

        original_write = self.audio.stream.write

        def write_then_stop(data):
            original_write(data)
            stop_event.set()

        self.audio.stream.write = write_then_stop
        metrics = engine.speak("Stop", stop_event=stop_event)

        self.assertTrue(metrics.interrupted)
        self.assertEqual(metrics.frames_written, 1)
        self.assertEqual(self.audio.stream.aborted, 1)
        self.assertEqual(self.jaw[-1], 0.25)

    def test_close_is_idempotent(self):
        engine = self.make_engine([])
        engine.close()
        engine.close()

        self.assertEqual(self.audio.stream.closed, 1)


if __name__ == "__main__":
    unittest.main()
