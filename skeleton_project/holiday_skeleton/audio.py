"""Small, testable helpers for microphone speech gating."""

from __future__ import annotations

import collections
from dataclasses import dataclass
from typing import Deque, Optional

import numpy as np


def select_input_device(devices, configured_device=None):
    """Return one input-capable device index and name.

    A configured value may be a numeric index, an exact device name, or a
    unique case-insensitive name substring.  An invalid or ambiguous explicit
    selection fails closed instead of silently listening to a headset adapter
    or another unintended input.
    """

    devices = list(devices)
    available = [
        (index, str(device.get("name", "mic")))
        for index, device in enumerate(devices)
        if int(device.get("max_input_channels", 0)) > 0
    ]
    if configured_device is None or str(configured_device).strip() == "":
        return available[0] if available else (None, "none")

    configured = str(configured_device).strip()
    try:
        index = int(configured)
    except ValueError:
        index = None

    if index is not None:
        if index < 0 or index >= len(devices):
            raise ValueError(f"input device index is out of range: {index}")
        device = devices[index]
        if int(device.get("max_input_channels", 0)) <= 0:
            raise ValueError(f"configured device has no input channels: {index}")
        return index, str(device.get("name", "mic"))

    needle = configured.casefold()
    exact = [item for item in available if item[1].casefold() == needle]
    matches = exact or [item for item in available if needle in item[1].casefold()]
    if not matches:
        raise ValueError(f"configured input device was not found: {configured!r}")
    if len(matches) > 1:
        names = ", ".join(name for _, name in matches)
        raise ValueError(
            f"configured input device is ambiguous: {configured!r} ({names})"
        )
    return matches[0]


def output_stream_format(
    audio_module,
    output_device=None,
    requested_sample_rate=None,
):
    """Choose a native int16 output rate and the smallest supported channel count."""

    device = audio_module.query_devices(output_device, "output")
    maximum_channels = int(device.get("max_output_channels", 0))
    if maximum_channels <= 0:
        raise ValueError("configured output device has no output channels")

    if requested_sample_rate in (None, ""):
        sample_rate = int(round(float(device.get("default_samplerate", 0))))
    else:
        try:
            sample_rate = int(requested_sample_rate)
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"output sample rate must be an integer: {requested_sample_rate!r}"
            ) from error
    if sample_rate <= 0:
        raise ValueError(f"invalid output sample rate: {sample_rate}")

    last_error = None
    for channels in range(1, maximum_channels + 1):
        try:
            audio_module.check_output_settings(
                device=output_device,
                channels=channels,
                dtype="int16",
                samplerate=sample_rate,
            )
        except Exception as error:  # PortAudio uses backend-specific exceptions.
            last_error = error
            continue
        return sample_rate, channels

    detail = f": {last_error}" if last_error is not None else ""
    raise ValueError(
        f"output device does not support {sample_rate} Hz int16 PCM{detail}"
    )


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
