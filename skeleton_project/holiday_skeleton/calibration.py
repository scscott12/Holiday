"""Validated hardware calibration values and a staged operator session."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Mapping, Optional


class CalibrationConfigError(ValueError):
    """Raised when calibration input is unsafe or internally inconsistent."""


class CalibrationStep(str, Enum):
    JAW_REST = "jaw_rest"
    JAW_MAX = "jaw_max"
    EYES_INVERTED = "eyes_inverted"
    EYES_DIM = "eyes_dim"
    EYES_FULL = "eyes_full"
    MICROPHONE_GATE = "microphone_gate"
    SPEAKER_VOLUME = "speaker_volume"
    PIR_HOLD = "pir_hold"
    PIR_COOLDOWN = "pir_cooldown"


CALIBRATION_STEPS = tuple(CalibrationStep)

CALIBRATION_INSTRUCTIONS = {
    CalibrationStep.JAW_REST: (
        "Set the closed-jaw rest position, then preview and verify the linkage is relaxed."
    ),
    CalibrationStep.JAW_MAX: (
        "Increase the maximum jaw position gradually; stop before the linkage binds."
    ),
    CalibrationStep.EYES_INVERTED: (
        "Choose normal or inverted polarity so logical off is physically dark."
    ),
    CalibrationStep.EYES_DIM: (
        "Choose the listening/idle accent level; preview is capped for safety."
    ),
    CalibrationStep.EYES_FULL: (
        "Choose the speaking eye level; it must not be below the dim level."
    ),
    CalibrationStep.MICROPHONE_GATE: (
        "Measure ambient noise, then set the speech gate above the reported room level."
    ),
    CalibrationStep.SPEAKER_VOLUME: (
        "Preview the calibration phrase and choose a clear level without distortion."
    ),
    CalibrationStep.PIR_HOLD: (
        "Set how long motion must remain present before a visit starts."
    ),
    CalibrationStep.PIR_COOLDOWN: (
        "Set the minimum quiet interval between completed visitor sessions."
    ),
}


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalibrationConfigError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise CalibrationConfigError(
            f"{label} must be between {minimum:g} and {maximum:g}"
        )
    return number


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise CalibrationConfigError(f"{label} must be true or false")
    return value


@dataclass(frozen=True)
class HardwareCalibration:
    """Persistent values that tune physical input and output boundaries."""

    jaw_rest: float = 0.25
    jaw_max: float = 1.0
    eyes_inverted: bool = False
    microphone_gate: float = 180.0
    pir_hold_seconds: float = 0.8
    pir_cooldown_seconds: float = 8.0

    def validated(self) -> "HardwareCalibration":
        jaw_rest = _number(self.jaw_rest, "jaw_rest", 0.0, 0.7)
        jaw_max = _number(self.jaw_max, "jaw_max", 0.1, 1.0)
        if jaw_max - jaw_rest < 0.05:
            raise CalibrationConfigError(
                "jaw_max must be at least 0.05 above jaw_rest"
            )
        return HardwareCalibration(
            jaw_rest=jaw_rest,
            jaw_max=jaw_max,
            eyes_inverted=_boolean(self.eyes_inverted, "eyes_inverted"),
            microphone_gate=_number(
                self.microphone_gate,
                "microphone_gate",
                25.0,
                5000.0,
            ),
            pir_hold_seconds=_number(
                self.pir_hold_seconds,
                "pir_hold_seconds",
                0.1,
                5.0,
            ),
            pir_cooldown_seconds=_number(
                self.pir_cooldown_seconds,
                "pir_cooldown_seconds",
                1.0,
                120.0,
            ),
        )

    def to_payload(self) -> dict[str, Any]:
        calibrated = self.validated()
        return {
            "jaw_rest": calibrated.jaw_rest,
            "jaw_max": calibrated.jaw_max,
            "eyes_inverted": calibrated.eyes_inverted,
            "microphone_gate": calibrated.microphone_gate,
            "pir_hold_seconds": calibrated.pir_hold_seconds,
            "pir_cooldown_seconds": calibrated.pir_cooldown_seconds,
        }


DEFAULT_HARDWARE_CALIBRATION = HardwareCalibration()


def hardware_calibration_from_payload(value: Any) -> HardwareCalibration:
    if not isinstance(value, dict):
        raise CalibrationConfigError("calibration must be an object")
    expected = {
        "jaw_rest",
        "jaw_max",
        "eyes_inverted",
        "microphone_gate",
        "pir_hold_seconds",
        "pir_cooldown_seconds",
    }
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise CalibrationConfigError(
            "calibration has unknown fields: " + ", ".join(unknown)
        )
    if missing:
        raise CalibrationConfigError(
            "calibration is missing fields: " + ", ".join(missing)
        )
    return HardwareCalibration(
        jaw_rest=value["jaw_rest"],
        jaw_max=value["jaw_max"],
        eyes_inverted=value["eyes_inverted"],
        microphone_gate=value["microphone_gate"],
        pir_hold_seconds=value["pir_hold_seconds"],
        pir_cooldown_seconds=value["pir_cooldown_seconds"],
    ).validated()


@dataclass(frozen=True)
class CalibrationValues:
    """One complete calibration draft, including existing operator levels."""

    hardware: HardwareCalibration
    eyes_dim: float
    eyes_full: float
    speaker_volume: float

    def validated(self) -> "CalibrationValues":
        eyes_dim = _number(self.eyes_dim, "eyes_dim", 0.0, 1.0)
        eyes_full = _number(self.eyes_full, "eyes_full", 0.0, 1.0)
        if eyes_full < eyes_dim:
            raise CalibrationConfigError("eyes_full must not be below eyes_dim")
        return CalibrationValues(
            hardware=self.hardware.validated(),
            eyes_dim=eyes_dim,
            eyes_full=eyes_full,
            speaker_volume=_number(
                self.speaker_volume,
                "speaker_volume",
                0.0,
                2.0,
            ),
        )

    def value_for(self, step: CalibrationStep) -> float | bool:
        values = {
            CalibrationStep.JAW_REST: self.hardware.jaw_rest,
            CalibrationStep.JAW_MAX: self.hardware.jaw_max,
            CalibrationStep.EYES_INVERTED: self.hardware.eyes_inverted,
            CalibrationStep.EYES_DIM: self.eyes_dim,
            CalibrationStep.EYES_FULL: self.eyes_full,
            CalibrationStep.MICROPHONE_GATE: self.hardware.microphone_gate,
            CalibrationStep.SPEAKER_VOLUME: self.speaker_volume,
            CalibrationStep.PIR_HOLD: self.hardware.pir_hold_seconds,
            CalibrationStep.PIR_COOLDOWN: self.hardware.pir_cooldown_seconds,
        }
        return values[CalibrationStep(step)]

    def with_value(self, step: CalibrationStep, value: Any) -> "CalibrationValues":
        step = CalibrationStep(step)
        hardware = self.hardware
        if step is CalibrationStep.JAW_REST:
            hardware = replace(
                hardware,
                jaw_rest=_number(value, "jaw_rest", 0.0, 0.7),
            )
        elif step is CalibrationStep.JAW_MAX:
            hardware = replace(
                hardware,
                jaw_max=_number(value, "jaw_max", 0.1, 1.0),
            )
        elif step is CalibrationStep.EYES_INVERTED:
            hardware = replace(
                hardware,
                eyes_inverted=_boolean(value, "eyes_inverted"),
            )
        elif step is CalibrationStep.EYES_DIM:
            return replace(
                self,
                eyes_dim=_number(value, "eyes_dim", 0.0, 1.0),
            )
        elif step is CalibrationStep.EYES_FULL:
            return replace(
                self,
                eyes_full=_number(value, "eyes_full", 0.0, 1.0),
            )
        elif step is CalibrationStep.MICROPHONE_GATE:
            hardware = replace(
                hardware,
                microphone_gate=_number(value, "microphone_gate", 25.0, 5000.0),
            )
        elif step is CalibrationStep.SPEAKER_VOLUME:
            return replace(
                self,
                speaker_volume=_number(value, "speaker_volume", 0.0, 2.0),
            )
        elif step is CalibrationStep.PIR_HOLD:
            hardware = replace(
                hardware,
                pir_hold_seconds=_number(value, "pir_hold_seconds", 0.1, 5.0),
            )
        elif step is CalibrationStep.PIR_COOLDOWN:
            hardware = replace(
                hardware,
                pir_cooldown_seconds=_number(
                    value,
                    "pir_cooldown_seconds",
                    1.0,
                    120.0,
                ),
            )
        return replace(self, hardware=hardware)


class CalibrationSession:
    """Stage a complete calibration until the caller commits or cancels it."""

    def __init__(self) -> None:
        self._active = False
        self._step = CALIBRATION_STEPS[0]
        self._original: Optional[CalibrationValues] = None
        self._staged: Optional[CalibrationValues] = None

    @property
    def active(self) -> bool:
        return self._active

    @property
    def step(self) -> CalibrationStep:
        return self._step

    @property
    def instruction(self) -> str:
        return CALIBRATION_INSTRUCTIONS[self._step]

    @property
    def staged(self) -> CalibrationValues:
        if not self._active or self._staged is None:
            raise CalibrationConfigError("no calibration session is active")
        return self._staged

    @property
    def original(self) -> CalibrationValues:
        if not self._active or self._original is None:
            raise CalibrationConfigError("no calibration session is active")
        return self._original

    def start(self, current: CalibrationValues) -> CalibrationValues:
        if self._active:
            raise CalibrationConfigError("calibration session is already active")
        validated = current.validated()
        self._active = True
        self._step = CALIBRATION_STEPS[0]
        self._original = validated
        self._staged = validated
        return validated

    def select(self, step: CalibrationStep | str) -> CalibrationStep:
        if not self._active:
            raise CalibrationConfigError("start calibration before selecting a step")
        try:
            self._step = CalibrationStep(step)
        except ValueError as error:
            raise CalibrationConfigError(f"unknown calibration step {step!r}") from error
        return self._step

    def update(self, step: CalibrationStep | str, value: Any) -> CalibrationValues:
        selected = self.select(step)
        self._staged = self.staged.with_value(selected, value)
        return self._staged

    def next(self) -> CalibrationStep:
        if not self._active:
            raise CalibrationConfigError("start calibration before advancing")
        index = CALIBRATION_STEPS.index(self._step)
        self._step = CALIBRATION_STEPS[min(index + 1, len(CALIBRATION_STEPS) - 1)]
        return self._step

    def validated(self) -> CalibrationValues:
        return self.staged.validated()

    def complete(self) -> CalibrationValues:
        values = self.validated()
        self._clear()
        return values

    def cancel(self) -> Optional[CalibrationValues]:
        original = self._original
        self._clear()
        return original

    def _clear(self) -> None:
        self._active = False
        self._step = CALIBRATION_STEPS[0]
        self._original = None
        self._staged = None
