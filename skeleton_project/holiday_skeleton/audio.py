"""Small, testable helpers for microphone speech gating."""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Deque, Optional

import numpy as np


def resample_linear_int16(
    samples: np.ndarray,
    source_rate: int,
    target_rate: int,
    state: dict,
) -> np.ndarray:
    """Resample a stream of mono int16 blocks while retaining edge state."""

    samples = np.asarray(samples, dtype=np.int16)
    if source_rate == target_rate or samples.size == 0:
        return samples

    previous = state.get("previous", np.zeros(0, dtype=np.int16))
    combined = np.concatenate((previous, samples))
    if combined.size < 2:
        state["previous"] = combined
        return np.zeros(0, dtype=np.int16)

    ratio = target_rate / float(source_rate)
    phase = float(state.get("phase", 0.0))
    output_length = int(np.floor((len(combined) - 1 - phase) * ratio))
    if output_length <= 0:
        state["previous"] = combined
        state["phase"] = phase
        return np.zeros(0, dtype=np.int16)

    positions = phase + np.arange(output_length) / ratio
    lower = np.floor(positions).astype(np.int32)
    upper = np.clip(lower + 1, 0, len(combined) - 1)
    fraction = (positions - lower).astype(np.float32)
    output = (
        combined[lower].astype(np.float32) * (1.0 - fraction)
        + combined[upper].astype(np.float32) * fraction
    )
    output = np.clip(np.round(output), -32768, 32767).astype(np.int16)
    state["previous"] = combined[lower[-1] + 1 :]
    state["phase"] = positions[-1] - lower[-1]
    return output


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
