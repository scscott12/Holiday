"""Hardware-free unit tests for speech synthesis, PCM playback, and jaw motion."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from holiday_skeleton.speech import (
    PiperSpeechEngine,
    SpeechEngineError,
    SpeechMetrics,
    jaw_envelope,
    scale_pcm16,
    split_pcm16_frames,
)


class FakeChunk:
    """Stand-in for a Piper synthesis chunk carrying int16 PCM audio."""

    sample_rate = 1000
    sample_width = 2
    sample_channels = 1

    def __init__(self, samples):
        self.audio_int16_bytes = np.asarray(samples, dtype=np.int16).tobytes()


class FakeVoice:
    """Yields pre-built chunks so tests never need a real Piper model."""

    config = SimpleNamespace(sample_rate=1000)

    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        yield from self.chunks


class FakeStream:
    """Records writes and lifecycle calls without touching PortAudio."""

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
    """Supplies a single FakeStream, emulating the persistent output stream."""

    def __init__(self):
        self.stream = FakeStream()
        self.created = []

    def RawOutputStream(self, **kwargs):
        self.created.append(kwargs)
        return self.stream


class SpeechSynthesisTests(unittest.TestCase):
    """Verify Piper synthesis produces the expected PCM output."""

    def make_engine(self, chunks, volume=1.0, output_sample_rate=None,
                    output_channels=1, rest_fraction=0.25, maximum_fraction=1.0):
        self.voice = FakeVoice(chunks)
        self.audio = FakeAudio()
        self.jaw = []
        self.volume = [volume]
        engine = PiperSpeechEngine(
            voice=self.voice,
            audio_module=self.audio,
            jaw_set=self.jaw.append,
            volume_getter=lambda: self.volume[0],
            rest_fraction=rest_fraction,
            maximum_fraction=maximum_fraction,
            output_sample_rate=output_sample_rate,
            output_channels=output_channels,
            frame_ms=10,
        )
        return engine

    def test_synthesis_plays_pcm_from_voice(self):
        """Synthesized speech produces PCM frames written to the stream."""
        samples = np.full(20, 5000, dtype=np.int16)
        engine = self.make_engine([FakeChunk(samples)])

        metrics = engine.speak("Hello, visitor!")

        played = b"".join(self.audio.stream.writes)
        np.testing.assert_array_equal(
            np.frombuffer(played, dtype=np.int16),
            samples,
        )
        self.assertEqual(metrics.frames_written, 2)
        self.assertEqual(metrics.phrases_spoken, 1)
        self.assertFalse(metrics.interrupted)

    def test_synthesis_records_voice_calls(self):
        """The voice is invoked once per speak call with the correct text."""
        engine = self.make_engine([FakeChunk(np.full(10, 1000, dtype=np.int16))])

        engine.speak("Ahoy, matey!")

        self.assertEqual(self.voice.calls, ["Ahoy, matey!"])

    def test_synthesis_applies_volume_scaling(self):
        """Volume from the getter is applied to every PCM frame."""
        engine = self.make_engine(
            [FakeChunk(np.full(20, 10000, dtype=np.int16))],
            volume=0.5,
        )

        engine.speak("Loud line")

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        )
        np.testing.assert_array_equal(played, np.full(20, 5000, dtype=np.int16))

    def test_synthesis_clips_volume_overflow(self):
        """Volume scaling clips at int16 bounds instead of wrapping."""
        engine = self.make_engine(
            [FakeChunk(np.array([20000, -20000], dtype=np.int16))],
            volume=2.0,
        )

        engine.speak("Clamp")

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        )
        np.testing.assert_array_equal(
            played, np.array([32767, -32768], dtype=np.int16)
        )

    def test_synthesis_empty_text_produces_no_output(self):
        """Empty or whitespace-only text produces no audio at all."""
        engine = self.make_engine([FakeChunk(np.full(10, 1000, dtype=np.int16))])
        first_audio = []

        metrics = engine.speak("   ", first_audio=first_audio.append)

        self.assertEqual(metrics, SpeechMetrics(0.0, 0.0, 0.0, 0))
        self.assertEqual(self.voice.calls, [])
        self.assertEqual(self.audio.stream.writes, [])
        self.assertEqual(first_audio, [])

    def test_synthesis_failure_propagates_with_audio_state(self):
        """A failing voice raises SpeechEngineError with correct audio_started."""
        chunk = FakeChunk(np.full(10, 1000, dtype=np.int16))
        engine = self.make_engine([chunk])
        failure = RuntimeError("voice synthesis failed")

        def failing_synthesis(text):
            yield chunk
            raise failure

        with patch.object(self.voice, "synthesize", failing_synthesis):
            with self.assertRaises(SpeechEngineError) as raised:
                engine.speak("Broken line.")

        self.assertEqual(raised.exception.args[0], "voice synthesis failed")
        self.assertTrue(raised.exception.audio_started)
        self.assertEqual(self.jaw[-1], 0.25)

    def test_synthesis_reports_first_audio_latency(self):
        """first_audio callback fires with a non-negative latency value."""
        engine = self.make_engine([FakeChunk(np.full(20, 1000, dtype=np.int16))])
        first_audio = []

        engine.speak("Greeting", first_audio=first_audio.append)

        self.assertEqual(len(first_audio), 1)
        self.assertGreaterEqual(first_audio[0], 0.0)

    def test_synthesis_reuses_single_output_stream(self):
        """Multiple speak calls share the one persistent stream."""
        engine = self.make_engine([FakeChunk(np.full(20, 1000, dtype=np.int16))])

        engine.speak("First")
        engine.speak("Second")

        self.assertEqual(len(self.audio.created), 1)
        self.assertEqual(self.audio.stream.started, 3)
        self.assertEqual(self.jaw[-1], 0.25)

    def test_synthesis_warm_up_materializes_without_playing(self):
        """Warm-up runs inference but writes nothing to the stream."""
        engine = self.make_engine([FakeChunk(np.full(20, 1000, dtype=np.int16))])

        elapsed = engine.warm_up("Ready")

        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(self.voice.calls, ["Ready"])
        self.assertEqual(self.audio.stream.writes, [])


class PcmPlaybackTests(unittest.TestCase):
    """Verify direct PCM playback through the persistent stream."""

    def make_engine(self, output_channels=1, rest_fraction=0.25,
                    maximum_fraction=1.0):
        self.voice = FakeVoice([])
        self.audio = FakeAudio()
        self.jaw = []
        self.volume = [1.0]
        engine = PiperSpeechEngine(
            voice=self.voice,
            audio_module=self.audio,
            jaw_set=self.jaw.append,
            volume_getter=lambda: self.volume[0],
            rest_fraction=rest_fraction,
            maximum_fraction=maximum_fraction,
            output_channels=output_channels,
            frame_ms=10,
        )
        return engine

    def test_play_pcm16_writes_exact_bytes(self):
        """PCM bytes are written to the stream unchanged at unit volume."""
        engine = self.make_engine()
        pcm = np.full(20, 3000, dtype=np.int16).tobytes()

        metrics = engine.play_pcm16(pcm)

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        )
        np.testing.assert_array_equal(played, np.full(20, 3000, dtype=np.int16))
        self.assertEqual(metrics.frames_written, 2)
        self.assertAlmostEqual(metrics.audio_seconds, 0.02)

    def test_play_pcm16_applies_volume(self):
        """Volume getter scales PCM output in play_pcm16."""
        engine = self.make_engine()
        self.volume[0] = 0.25
        pcm = np.full(20, 10000, dtype=np.int16).tobytes()

        engine.play_pcm16(pcm)

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        )
        np.testing.assert_array_equal(
            played, np.full(20, 2500, dtype=np.int16)
        )

    def test_play_pcm16_volume_multiplier(self):
        """volume_multiplier scales on top of the volume getter."""
        engine = self.make_engine()
        self.volume[0] = 0.5
        pcm = np.full(20, 10000, dtype=np.int16).tobytes()

        engine.play_pcm16(pcm, volume_multiplier=0.5)

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        )
        np.testing.assert_array_equal(
            played, np.full(20, 2500, dtype=np.int16)
        )

    def test_play_pcm16_dups_to_stereo(self):
        """Mono PCM is duplicated for stereo output."""
        engine = self.make_engine(output_channels=2)
        pcm = np.arange(20, dtype=np.int16).tobytes()

        engine.play_pcm16(pcm)

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        ).reshape(-1, 2)
        np.testing.assert_array_equal(played[:, 0], played[:, 1])
        np.testing.assert_array_equal(
            played[:, 0], np.arange(20, dtype=np.int16)
        )

    def test_play_pcm16_clips_overflow(self):
        """Volume multiplier above 1.0 clips at int16 bounds."""
        engine = self.make_engine()
        pcm = np.array([20000, -20000], dtype=np.int16).tobytes()

        engine.play_pcm16(pcm, volume_multiplier=2.0)

        played = np.frombuffer(
            b"".join(self.audio.stream.writes), dtype=np.int16
        )
        np.testing.assert_array_equal(
            played, np.array([32767, -32768], dtype=np.int16)
        )

    def test_play_pcm16_first_audio_latency(self):
        """first_audio callback fires for the first frame of PCM playback."""
        engine = self.make_engine()
        first_audio = []
        pcm = np.full(30, 1000, dtype=np.int16).tobytes()

        engine.play_pcm16(pcm, first_audio=first_audio.append)

        self.assertEqual(len(first_audio), 1)
        self.assertGreaterEqual(first_audio[0], 0.0)

    def test_play_pcm16_reports_frames_written(self):
        """Frames written count matches the number of split frames."""
        engine = self.make_engine()
        pcm = np.full(40, 1000, dtype=np.int16).tobytes()

        metrics = engine.play_pcm16(pcm)

        # 40 samples * 2 bytes / 10 samples per frame = 2 frames (at 10ms/1000Hz)
        self.assertEqual(metrics.frames_written, 4)
        self.assertAlmostEqual(metrics.audio_seconds, 0.04)

    def test_play_pcm16_on_closed_engine_raises(self):
        """Playback on a closed engine raises SpeechEngineError."""
        engine = self.make_engine()
        engine.close()

        with self.assertRaises(SpeechEngineError):
            engine.play_pcm16(b"\x00\x00")


class JawMovementTests(unittest.TestCase):
    """Verify the jaw moves during speech and rests when idle."""

    def make_engine(self, chunks, rest_fraction=0.25, maximum_fraction=1.0):
        self.voice = FakeVoice(chunks)
        self.audio = FakeAudio()
        self.jaw = []
        engine = PiperSpeechEngine(
            voice=self.voice,
            audio_module=self.audio,
            jaw_set=self.jaw.append,
            volume_getter=lambda: 1.0,
            rest_fraction=rest_fraction,
            maximum_fraction=maximum_fraction,
            frame_ms=10,
        )
        return engine

    def test_jaw_moves_during_speech(self):
        """Loud speech causes the jaw to move beyond rest position."""
        # 1000 amplitude produces RMS ~ 1000, well above noise floor of 90
        loud_samples = np.full(20, 1000, dtype=np.int16)
        engine = self.make_engine([FakeChunk(loud_samples)])

        engine.speak("Loud speech")

        # Some jaw value should exceed rest fraction (0.25)
        self.assertGreater(max(self.jaw), 0.25)

    def test_jaw_rests_after_speech(self):
        """The jaw returns to rest fraction after speech completes."""
        engine = self.make_engine(
            [FakeChunk(np.full(20, 1000, dtype=np.int16))]
        )

        engine.speak("Greeting")

        self.assertEqual(self.jaw[-1], 0.25)

    def test_jaw_rests_after_pcm_playback(self):
        """The jaw returns to rest after PCM cue playback."""
        engine = self.make_engine(
            [FakeChunk(np.full(20, 1000, dtype=np.int16))]
        )
        pcm = np.full(20, 1000, dtype=np.int16).tobytes()

        engine.play_pcm16(pcm, animate_jaw=True)

        self.assertEqual(self.jaw[-1], 0.25)

    def test_jaw_not_animated_without_flag(self):
        """PCM playback without animate_jaw does not move the jaw."""
        engine = self.make_engine(
            [FakeChunk(np.full(20, 1000, dtype=np.int16))]
        )
        pcm = np.full(20, 1000, dtype=np.int16).tobytes()
        jaw_before = len(self.jaw)

        engine.play_pcm16(pcm, animate_jaw=False)

        # Only rest calls from finally/stop: no intermediate jaw moves
        self.assertEqual(len(self.jaw), jaw_before + 1)
        self.assertEqual(self.jaw[-1], 0.25)

    def test_jaw_envelope_tracks_loudness(self):
        """jaw_envelope produces higher levels for louder frames."""
        quiet = np.full(10, 100, dtype=np.int16).tobytes()
        loud = np.full(10, 1000, dtype=np.int16).tobytes()

        levels = jaw_envelope([quiet, loud])

        self.assertLess(levels[0], levels[1])
        self.assertGreater(levels[1], 0.5)

    def test_jaw_envelope_empty_input(self):
        """jaw_envelope returns zeros for empty frame list."""
        levels = jaw_envelope([])

        self.assertEqual(len(levels), 0)

    def test_jaw_scale_pcm16_clips(self):
        """scale_pcm16 clips instead of wrapping on overflow."""
        frame = np.array([20000, -20000], dtype=np.int16).tobytes()
        scaled = np.frombuffer(scale_pcm16(frame, 2.0), dtype=np.int16)

        np.testing.assert_array_equal(
            scaled, np.array([32767, -32768], dtype=np.int16)
        )

    def test_jaw_scale_pcm16_unit_passthrough(self):
        """scale_pcm16 at volume 1.0 returns the frame unchanged."""
        frame = np.array([1000, -1000], dtype=np.int16).tobytes()

        self.assertEqual(scale_pcm16(frame, 1.0), frame)

    def test_jaw_close_rests(self):
        """Closing the engine returns the jaw to rest fraction."""
        engine = self.make_engine(
            [FakeChunk(np.full(20, 1000, dtype=np.int16))]
        )

        engine.close()

        self.assertEqual(self.jaw[-1], 0.25)

    def test_split_pcm16_frames_alignment(self):
        """split_pcm16_frames produces sample-aligned frame blocks."""
        samples = np.arange(25, dtype=np.int16)
        frames = split_pcm16_frames(
            samples.tobytes(), 1000, frame_ms=10
        )

        self.assertEqual([len(frame) for frame in frames], [20, 20, 10])
        np.testing.assert_array_equal(
            np.frombuffer(b"".join(frames), dtype=np.int16), samples
        )


if __name__ == "__main__":
    unittest.main()
