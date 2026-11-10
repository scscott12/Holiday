"""Barge-in corroboration: an uncorroborated command must be rejected.

A constrained grammar can force nearby speaker playback into its nearest
command. A barge-in must therefore be confirmed by a *second* source (the
unrestricted verification decoder) hearing the same command, or, when no
corroborating source is configured at all, must not be accepted on the
constrained decoder alone.
"""

import json
import threading
import unittest

import numpy as np

from holiday_skeleton.barge_in import BargeInMatcher, BargeInMonitor


class FakeRecognizer:
    """Constrained decoder that always hears the command but never finalizes."""

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


class Timeline:
    """Fake clock keyed to the block being processed, not to call counts."""

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


class BargeInCorroborationTests(unittest.TestCase):
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

    def test_barge_in_without_corroboration_is_rejected(self):
        # The constrained decoder hears "stop" on every block, but the
        # unrestricted second source never confirms the command: the
        # barge-in must be rejected and the visit must not be interrupted.
        monitor, result = self.run_monitor(
            times=[0.0, 1.0, 2.0],
            constrained=["stop", "stop", "stop"],
            verified=["", "", ""],
        )

        self.assertIsNone(result)
        self.assertFalse(monitor.interrupt_event.is_set())

    def test_no_corroborating_source_rejects_a_bare_command(self):
        # No verification factory is configured, so nothing can corroborate.
        # The bare constrained command must not by itself interrupt the visit.
        monitor = BargeInMonitor(
            audio_module=FakeAudio(),
            recognizer_factory=lambda grammar: FakeRecognizer(),
            matcher=BargeInMatcher(),
            input_device=1,
            capture_rate=1000,
            recognition_rate=1000,
            blocksize=100,
            energy_threshold=10,
            minimum_voiced_seconds=0.05,
            partial_confirmations=2,
            require_corroboration=True,
        ).start()

        self.assertFalse(monitor.interrupt_event.wait(timeout=1.0))
        self.assertIsNone(monitor.stop())

    def test_corroborated_command_is_accepted(self):
        # A second source that hears the same command within the window
        # corroborates it, so the barge-in is accepted.
        monitor, result = self.run_monitor(
            times=[0.0, 1.0, 2.0],
            constrained=["stop"] * 3,
            verified=["", "", "stop"],
        )

        self.assertIsNotNone(result)
        self.assertEqual(result.transcript, "stop")
        self.assertTrue(monitor.interrupt_event.is_set())


if __name__ == "__main__":
    unittest.main()
