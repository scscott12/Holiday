"""Small, testable helpers for microphone speech gating."""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Deque, Optional

import numpy as np


@dataclass(frozen=True)
class GateResult:
    """Audio that should be sent to Vosk for one microphone block."""

    audio: np.ndarray
    speech_started: bool = False


class SpeechGate:
    """Hold preroll until speech begins, then pass every sample exactly once."""

    def __init__(
        self,
        sample_rate: int,
        energy_threshold: float,
        preroll_seconds: float,
        minimum_voiced_seconds: float,
        end_silence_seconds: float,
    ) -> None:
        self.sample_rate = sample_rate
        self.energy_threshold = energy_threshold
        self.minimum_voiced_seconds = minimum_voiced_seconds
        self.end_silence_seconds = end_silence_seconds
        self._preroll: Deque[int] = collections.deque(
            maxlen=max(1, int(sample_rate * preroll_seconds))
        )
        self._voiced_seconds = 0.0
        self._last_voice_at: Optional[float] = None
        self.speaking = False

    def process(self, samples: np.ndarray, now: float) -> GateResult:
        samples = np.asarray(samples, dtype=np.int16)
        if samples.size == 0:
            return GateResult(samples)

        duration = samples.size / float(self.sample_rate)
        energy = float(np.mean(np.abs(samples.astype(np.int32))))
        voiced = energy > self.energy_threshold

        if not self.speaking:
            self._preroll.extend(samples.tolist())
            if voiced:
                self._voiced_seconds += duration
            else:
                self._voiced_seconds = max(0.0, self._voiced_seconds - duration)

            if self._voiced_seconds < self.minimum_voiced_seconds:
                return GateResult(np.zeros(0, dtype=np.int16))

            self.speaking = True
            self._last_voice_at = now
            audio = np.asarray(self._preroll, dtype=np.int16)
            self._preroll.clear()
            return GateResult(audio, speech_started=True)

        if voiced:
            self._last_voice_at = now
        return GateResult(samples)

    def silence_complete(self, now: float) -> bool:
        return bool(
            self.speaking
            and self._last_voice_at is not None
            and now - self._last_voice_at >= self.end_silence_seconds
        )

