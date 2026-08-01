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

    def __init__(self, chunks, fail_on=()):
        self.chunks = chunks
        self.fail_on = set(fail_on)
        self.calls = []

    def synthesize(self, text):
        self.calls.append(text)
        if text in self.fail_on:
            raise RuntimeError("synthetic cache failure")
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
    def make_engine(self, chunks, volume=1.0):
        self.voice = FakeVoice(chunks)
        self.audio = FakeAudio()
        self.jaw = []
        self.volume = [volume]
        engine = PiperSpeechEngine(
            voice=self.voice,
            audio_module=self.audio,
            jaw_set=self.jaw.append,
            volume_getter=lambda: self.volume[0],
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

    def test_phrase_stream_keeps_output_open_and_reports_one_first_audio(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        first_audio = []

        metrics = engine.speak_phrases(
            iter(["First phrase.", "Second phrase."]),
            first_audio=first_audio.append,
        )

        self.assertEqual(self.voice.calls, ["First phrase.", "Second phrase."])
        self.assertEqual(metrics.phrases_spoken, 2)
        self.assertEqual(metrics.frames_written, 4)
        self.assertEqual(len(first_audio), 1)
        self.assertEqual(self.audio.stream.started, 2)

    def test_phrase_callback_reports_each_played_phrase(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        events = []

        engine.speak_phrases(
            ["First phrase.", "Second phrase."],
            phrase_started=events.append,
        )

        self.assertEqual(events, ["First phrase.", "Second phrase."])

    def test_warm_up_materializes_audio_without_playing_it(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])

        elapsed = engine.warm_up("Ready")

        self.assertGreaterEqual(elapsed, 0.0)
        self.assertEqual(self.voice.calls, ["Ready"])
        self.assertEqual(self.audio.stream.writes, [])

    def test_precache_renders_unique_lines_without_playing_them(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])

        metrics = engine.cache_phrases([
            "Ahoy there.",
            "  Ahoy   there. ",
            "Goodbye.",
            "",
        ])

        self.assertEqual(self.voice.calls, ["Ahoy there.", "Goodbye."])
        self.assertEqual(self.audio.stream.writes, [])
        self.assertEqual(metrics.requested_entries, 2)
        self.assertEqual(metrics.new_entries, 2)
        self.assertEqual(metrics.failed_entries, 0)
        self.assertEqual(metrics.total_entries, 2)
        self.assertEqual(metrics.audio_seconds, 0.04)
        self.assertEqual(metrics.pcm_bytes, 80)

    def test_cached_line_bypasses_piper_and_uses_current_volume(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        engine.cache_phrases(["Ahoy there."])
        self.volume[0] = 0.5

        metrics = engine.speak("  Ahoy   there. ")

        self.assertEqual(self.voice.calls, ["Ahoy there."])
        self.assertEqual(metrics.cached_phrases, 1)
        self.assertEqual(metrics.phrases_spoken, 1)
        played = np.frombuffer(b"".join(self.audio.stream.writes), dtype=np.int16)
        np.testing.assert_array_equal(played, np.full(20, 500, dtype=np.int16))

    def test_cache_miss_keeps_live_streaming_synthesis(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        engine.cache_phrases(["Opening line."])

        metrics = engine.speak("Dynamic answer.")

        self.assertEqual(self.voice.calls, ["Opening line.", "Dynamic answer."])
        self.assertEqual(metrics.cached_phrases, 0)
        self.assertEqual(metrics.frames_written, 2)

    def test_second_cache_warmup_reuses_existing_entries(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        engine.cache_phrases(["Opening line."])

        metrics = engine.cache_phrases(["Opening line."])

        self.assertEqual(self.voice.calls, ["Opening line."])
        self.assertEqual(metrics.new_entries, 0)
        self.assertEqual(metrics.existing_entries, 1)
        self.assertEqual(metrics.total_entries, 1)

    def test_cache_warmup_can_yield_between_phrases(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        stop_event = threading.Event()
        progress = []
        original_render = engine._render_for_cache

        def render_then_stop(text):
            rendered = original_render(text)
            stop_event.set()
            return rendered

        engine._render_for_cache = render_then_stop
        metrics = engine.cache_phrases(
            ["First line.", "Second line."],
            stop_event=stop_event,
            progress=lambda: progress.append(True),
        )

        self.assertTrue(metrics.interrupted)
        self.assertEqual(self.voice.calls, ["First line."])
        self.assertEqual(metrics.new_entries, 1)
        self.assertEqual(metrics.total_entries, 1)
        self.assertGreaterEqual(len(progress), 2)

    def test_cache_can_be_pruned_after_personality_switch(self):
        engine = self.make_engine([FakeChunk(np.full(20, 1000))])
        engine.cache_phrases(["Old greeting.", "Shared scene.", "New greeting."])

        removed = engine.retain_cached_phrases(["Shared scene.", "New greeting."])

        self.assertEqual(removed, 1)
        self.assertEqual(engine.cache_entries, 2)
        old_metrics = engine.speak("Old greeting.")
        shared_metrics = engine.speak("Shared scene.")
        self.assertEqual(old_metrics.cached_phrases, 0)
        self.assertEqual(shared_metrics.cached_phrases, 1)

    def test_one_cache_failure_does_not_disable_other_lines_or_live_tts(self):
        self.voice = FakeVoice(
            [FakeChunk(np.full(20, 1000))],
            fail_on={"Broken line."},
        )
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

        cache = engine.cache_phrases(["Good line.", "Broken line."])
        spoken = engine.speak("Good line.")

        self.assertEqual(cache.new_entries, 1)
        self.assertEqual(cache.failed_entries, 1)
        self.assertIn("synthetic cache failure", cache.errors[0])
        self.assertEqual(spoken.cached_phrases, 1)

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

    def test_pcm_cue_uses_persistent_stream_volume_and_optional_jaw(self):
        engine = self.make_engine([])
        self.volume[0] = 0.5
        pcm = np.full(20, 1000, dtype=np.int16).tobytes()

        metrics = engine.play_pcm16(
            pcm,
            animate_jaw=True,
            volume_multiplier=0.5,
        )

        played = np.frombuffer(b"".join(self.audio.stream.writes), dtype=np.int16)
        np.testing.assert_array_equal(played, np.full(20, 250, dtype=np.int16))
        self.assertEqual(metrics.frames_written, 2)
        self.assertGreater(max(self.jaw), 0.25)
        self.assertEqual(self.jaw[-1], 0.25)

    def test_pcm_cue_stops_within_one_frame(self):
        engine = self.make_engine([])
        stop_event = threading.Event()
        pcm = np.full(30, 1000, dtype=np.int16).tobytes()
        original_write = self.audio.stream.write

        def write_then_stop(data):
            original_write(data)
            stop_event.set()

        self.audio.stream.write = write_then_stop

        metrics = engine.play_pcm16(pcm, stop_event=stop_event)

        self.assertTrue(metrics.interrupted)
        self.assertEqual(metrics.frames_written, 1)
        self.assertEqual(self.audio.stream.aborted, 1)

    def test_close_is_idempotent(self):
        engine = self.make_engine([])
        engine.close()
        engine.close()

        self.assertEqual(self.audio.stream.closed, 1)
        self.assertEqual(engine.cache_entries, 0)


if __name__ == "__main__":
    unittest.main()
