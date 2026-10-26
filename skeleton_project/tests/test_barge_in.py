import json
import threading
import unittest
from unittest import mock

import numpy as np

from holiday_skeleton.audio import SpeechGate
from holiday_skeleton.barge_in import (
    AnyStopEvent,
    BargeInAction,
    BargeInDetector,
    BargeInMatcher,
    BargeInMonitor,
)


class BargeInMatcherTests(unittest.TestCase):
    def setUp(self):
        self.matcher = BargeInMatcher(
            stop_commands=("stop", "quiet"),
            listen_commands=("wait",),
            wake_words=("skeleton",),
        )

    def test_stop_and_quiet_end_the_visit(self):
        self.assertEqual(
            self.matcher.match("STOP!").action,
            BargeInAction.END_VISIT,
        )
        self.assertEqual(
            self.matcher.match("please quiet").action,
            BargeInAction.END_VISIT,
        )

    def test_wait_and_wake_name_return_to_listening(self):
        self.assertEqual(
            self.matcher.match("wait").action,
            BargeInAction.LISTEN,
        )
        self.assertEqual(
            self.matcher.match("hey skeleton").action,
            BargeInAction.LISTEN,
        )

    def test_prose_and_playback_echo_are_rejected(self):
        self.assertIsNone(self.matcher.match("do not stop talking"))
        self.assertIsNone(
            self.matcher.match("stop", expected_speech="Please stop by the dock.")
        )

    def test_vosk_grammar_is_command_only_with_unknown_fallback(self):
        grammar = self.matcher.grammar

        self.assertIn("stop", grammar)
        self.assertIn("hey skeleton stop", grammar)
        self.assertEqual(grammar[-1], "[unk]")

    def test_wake_required_mode_rejects_bare_commands(self):
        matcher = BargeInMatcher(require_wake_word=True)

        self.assertIsNone(matcher.match("stop"))
        self.assertEqual(
            matcher.match("skeleton stop").action,
            BargeInAction.END_VISIT,
        )
        self.assertEqual(
            matcher.match("hey skeleton").action,
            BargeInAction.LISTEN,
        )


class BargeInDetectorTests(unittest.TestCase):
    def test_partial_result_must_repeat_before_interrupting(self):
        times = iter((10.0, 10.12))
        detector = BargeInDetector(
            BargeInMatcher(),
            partial_confirmations=2,
            clock=lambda: next(times),
        )

        self.assertIsNone(detector.inspect("stop"))
        result = detector.inspect("stop")

        self.assertEqual(result.action, BargeInAction.END_VISIT)
        self.assertAlmostEqual(result.detected_seconds, 0.12)

    def test_final_result_interrupts_immediately(self):
        detector = BargeInDetector(BargeInMatcher(), partial_confirmations=3)

        result = detector.inspect("wait", final=True)

        self.assertEqual(result.action, BargeInAction.LISTEN)

    def test_unrelated_partial_breaks_confirmation_streak(self):
        detector = BargeInDetector(BargeInMatcher(), partial_confirmations=2)

        self.assertIsNone(detector.inspect("stop"))
        self.assertIsNone(detector.inspect("something else"))
        self.assertIsNone(detector.inspect("stop"))
        self.assertEqual(
            detector.inspect("stop").action,
            BargeInAction.END_VISIT,
        )

    def test_any_stop_event_combines_runtime_and_command_events(self):
        runtime = threading.Event()
        command = threading.Event()
        combined = AnyStopEvent(runtime, command)

        self.assertFalse(combined.is_set())
        command.set()
        self.assertTrue(combined.is_set())


class FakeRecognizer:
    def AcceptWaveform(self, data):
        return False

    def PartialResult(self):
        return json.dumps({"partial": "stop"})

    def Result(self):
        return json.dumps({"text": ""})


class FakeVerificationRecognizer(FakeRecognizer):
    def __init__(self, transcript):
        self.transcript = transcript

    def PartialResult(self):
        return json.dumps({"partial": self.transcript})


class FakeDelayedVerificationRecognizer(FakeRecognizer):
    def __init__(self, transcripts):
        self.transcripts = iter(transcripts)

    def PartialResult(self):
        return json.dumps({"partial": next(self.transcripts, "")})


class FakeInputStream:
    def __init__(self, callback, block_count=2):
        self.callback = callback
        self.block_count = block_count

    def __enter__(self):
        voiced = np.full(100, 1000, dtype=np.int16).tobytes()
        for _ in range(self.block_count):
            self.callback(voiced, 100, None, None)
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False


class FakeAudio:
    def __init__(self, block_count=2):
        self.block_count = block_count

    def RawInputStream(self, **kwargs):
        return FakeInputStream(kwargs["callback"], self.block_count)


class BargeInMonitorTests(unittest.TestCase):
    def test_monitor_sets_interrupt_event_from_live_audio_blocks(self):
        grammars = []

        def recognizer_factory(grammar):
            grammars.append(json.loads(grammar))
            return FakeRecognizer()

        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=recognizer_factory,
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
        ).start()

        self.assertTrue(monitor.interrupt_event.wait(timeout=1.0))
        result = monitor.stop()

        self.assertEqual(result.transcript, "stop")
        self.assertIn("[unk]", grammars[0])

    def test_full_vocabulary_verifier_rejects_constrained_echo_hallucination(self):
        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=lambda grammar: FakeRecognizer(),
            verification_recognizer_factory=lambda: FakeVerificationRecognizer(
                "ordinary generated speech"
            ),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
        ).start()

        self.assertFalse(monitor.interrupt_event.wait(timeout=0.1))
        self.assertIsNone(monitor.stop())

    def test_full_vocabulary_verifier_accepts_audible_command(self):
        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=lambda grammar: FakeRecognizer(),
            verification_recognizer_factory=lambda: FakeVerificationRecognizer(
                "please stop"
            ),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
        ).start()

        self.assertTrue(monitor.interrupt_event.wait(timeout=1.0))
        self.assertEqual(monitor.stop().transcript, "stop")

    def test_full_vocabulary_verifier_may_trail_command_decoder(self):
        monitor = BargeInMonitor(
            audio_module=FakeAudio(block_count=3),
            recognizer_factory=lambda grammar: FakeRecognizer(),
            verification_recognizer_factory=lambda: FakeDelayedVerificationRecognizer(
                ("", "", "stop")
            ),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
        ).start()

        self.assertTrue(monitor.interrupt_event.wait(timeout=1.0))
        self.assertEqual(monitor.stop().transcript, "stop")

    def test_full_vocabulary_verifier_accepts_wait_homophone(self):
        class WaitRecognizer(FakeRecognizer):
            def PartialResult(self):
                return json.dumps({"partial": "wait"})

        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=lambda grammar: WaitRecognizer(),
            verification_recognizer_factory=lambda: FakeVerificationRecognizer(
                "weight"
            ),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
        ).start()

        self.assertTrue(monitor.interrupt_event.wait(timeout=1.0))
        self.assertEqual(monitor.stop().transcript, "wait")

    def test_recent_expected_phrases_remain_available_across_boundaries(self):
        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=lambda grammar: FakeRecognizer(),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
        )

        monitor.set_expected_speech("Captain, hold fast.")
        monitor.set_expected_speech("The next phrase is playing now.")

        self.assertIn("Captain, hold fast.", monitor._expected())
        self.assertIn("The next phrase is playing now.", monitor._expected())
        self.assertIsNone(
            monitor.matcher.match("captain", expected_speech=monitor._expected())
        )


class Timeline:
    """Fake clock keyed to the block being processed, not to call counts.

    The clock reads ``times[n]`` while the recognizer has accepted ``n``
    blocks, so every clock read inside one block agrees.
    """

    def __init__(self, times):
        self.times = list(times)
        self.accepted = 0
        self.done = threading.Event()

    def clock(self):
        return self.times[min(self.accepted, len(self.times) - 1)]

    def block_accepted(self):
        self.accepted += 1
        if self.accepted >= len(self.times):
            self.done.set()


class TimelineRecognizer(FakeRecognizer):
    def __init__(self, timeline, partials, counts_blocks=False):
        self.timeline = timeline
        self.partials = list(partials)
        self.counts_blocks = counts_blocks
        self.calls = 0
        self._current = ""

    def AcceptWaveform(self, data):
        self._current = self.partials[min(self.calls, len(self.partials) - 1)]
        self.calls += 1
        if self.counts_blocks:
            self.timeline.block_accepted()
        return False

    def PartialResult(self):
        return json.dumps({"partial": self._current})


class CorroborationWindowTests(unittest.TestCase):
    def run_monitor(self, times, constrained, verified):
        timeline = Timeline(times)
        monitor = BargeInMonitor(
            audio_module=FakeAudio(block_count=len(times)),
            recognizer_factory=lambda grammar: TimelineRecognizer(
                timeline, constrained
            ),
            # The verifier runs after the constrained decoder in each block,
            # so it advances the timeline once the whole block is handled.
            verification_recognizer_factory=lambda: TimelineRecognizer(
                timeline, verified, counts_blocks=True
            ),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
            corroboration_seconds=1.0,
            clock=timeline.clock,
        ).start()
        self.assertTrue(timeline.done.wait(timeout=2.0))
        return monitor, monitor.stop()

    def test_corroboration_exactly_at_window_boundary_is_accepted(self):
        # Command is detected at t=1.0, so the window closes at t=2.0.
        monitor, result = self.run_monitor(
            times=[0.0, 1.0, 2.0],
            constrained=["stop"] * 3,
            verified=["", "", "stop"],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.transcript, "stop")
        self.assertTrue(monitor.interrupt_event.is_set())

    def test_corroboration_after_window_expires_does_not_interrupt(self):
        # The verifier would corroborate at t=2.5, but the window closed at 2.0.
        monitor, result = self.run_monitor(
            times=[0.0, 1.0, 2.5],
            constrained=["stop"] * 3,
            verified=["", "", "stop"],
        )

        self.assertIsNone(result)
        self.assertFalse(monitor.interrupt_event.is_set())

    def test_expired_window_is_rearmed_by_fresh_command(self):
        # Expiry at t=2.5 resets the detector; a new command then gets its own
        # window and may be corroborated.
        monitor, result = self.run_monitor(
            times=[0.0, 1.0, 2.5, 3.0],
            constrained=["stop"] * 4,
            verified=["", "", "", "stop"],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.transcript, "stop")
        self.assertEqual(result.detected_seconds, 0.5)  # from the re-armed detector

    def test_stale_verification_outside_window_is_not_reused(self):
        # Verifier heard "stop" at t=0 only; at t=1.5 it is older than 1.0s.
        monitor, result = self.run_monitor(
            times=[0.0, 1.5, 1.6],
            constrained=["", "stop", "stop"],
            verified=["stop", "", ""],
        )

        self.assertIsNone(result)

    def test_repeated_signals_while_interrupted_keep_first_result(self):
        class CountingEvent(threading.Event):
            sets = 0

            def set(self):
                CountingEvent.sets += 1
                super().set()

        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=lambda grammar: FakeRecognizer(),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
        )
        monitor.interrupt_event = CountingEvent()
        first = monitor.matcher.match("stop")
        later = monitor.matcher.match("wait")

        monitor._latch(first)
        monitor._latch(later)
        monitor._latch(first)

        self.assertIs(monitor.result, first)
        self.assertEqual(CountingEvent.sets, 1)
        self.assertTrue(monitor.interrupt_event.is_set())

    def test_detector_ignores_signals_after_interrupt(self):
        now = [10.0]
        detector = BargeInDetector(
            BargeInMatcher(), partial_confirmations=1, clock=lambda: now[0]
        )
        now[0] = 12.0
        first = detector.inspect("stop")
        now[0] = 99.0

        self.assertIs(detector.inspect("stop"), first)
        self.assertIs(detector.inspect("wait", final=True), first)
        self.assertIs(detector.inspect("", final=True), first)
        self.assertEqual(first.transcript, "stop")
        self.assertEqual(first.detected_seconds, 2.0)

    def test_monitor_gate_rearms_after_silence_following_speech(self):
        # Speech, then silence long enough to end the utterance: the monitor
        # must reset the gate so quiet blocks stop reaching the recognizer.
        timeline = Timeline([0.0, 1.0, 2.0])
        drained = threading.Event()
        gates = []

        class SpyGate(SpeechGate):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.processed = 0
                self.resets = 0
                gates.append(self)

            def process(self, samples, now):
                result = super().process(samples, now)
                self.processed += 1
                timeline.accepted = self.processed
                if self.processed == 3:
                    drained.set()
                return result

            def reset(self):
                self.resets += 1
                super().reset()

        class Stream(FakeInputStream):
            def __enter__(self):
                voiced = np.full(100, 1000, dtype=np.int16).tobytes()
                quiet = np.zeros(100, dtype=np.int16).tobytes()
                for block in (voiced, quiet, quiet):
                    self.callback(block, 100, None, None)
                return self

        class Audio:
            def RawInputStream(self, **kwargs):
                return Stream(kwargs["callback"])

        with mock.patch("holiday_skeleton.barge_in.SpeechGate", SpyGate):
            monitor = BargeInMonitor(
                audio_module=Audio(),
                recognizer_factory=lambda grammar: FakeRecognizer(),
                matcher=BargeInMatcher(),
                input_device=1,
                capture_rate=1000,
                recognition_rate=1000,
                blocksize=100,
                energy_threshold=10,
                minimum_voiced_seconds=0.05,
                end_silence_seconds=0.5,
                partial_confirmations=99,
                clock=timeline.clock,
            ).start()
            self.assertTrue(drained.wait(timeout=2.0))
            monitor.stop()

        self.assertEqual(gates[0].resets, 1)
        self.assertFalse(gates[0].speaking)


if __name__ == "__main__":
    unittest.main()
