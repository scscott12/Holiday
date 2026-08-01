"""Validated, interruptible scene definitions for the skeleton runtime."""

from __future__ import annotations

import json
import re
import time
import wave
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Mapping, Optional

import numpy as np


class SceneConfigError(ValueError):
    """A scene file is unsafe or does not match the supported schema."""


class SceneAction(str, Enum):
    SPEAK = "speak"
    PAUSE = "pause"
    EYES = "eyes"
    BLINK = "blink"
    FLICKER = "flicker"
    JAW = "jaw"
    SOUND = "sound"


@dataclass(frozen=True)
class SceneStep:
    action: SceneAction
    parameters: Mapping[str, Any]


@dataclass(frozen=True)
class Scene:
    name: str
    description: str
    steps: tuple[SceneStep, ...]


@dataclass(frozen=True)
class SceneResult:
    scene_name: str
    completed_steps: int
    total_steps: int
    duration_seconds: float
    interrupted: bool = False
    timed_out: bool = False
    error: str = ""

    @property
    def outcome(self) -> str:
        if self.error:
            return "error"
        if self.timed_out:
            return "timeout"
        if self.interrupted:
            return "interrupted"
        return "completed"


_SCENE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _number(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
) -> float:
    if isinstance(value, bool):
        raise SceneConfigError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise SceneConfigError(f"{field} must be a number") from error
    if not minimum <= result <= maximum:
        raise SceneConfigError(
            f"{field} must be between {minimum:g} and {maximum:g}"
        )
    return result


def _integer(
    value: Any,
    field: str,
    minimum: int,
    maximum: int,
) -> int:
    result = _number(value, field, minimum, maximum)
    if not result.is_integer():
        raise SceneConfigError(f"{field} must be a whole number")
    return int(result)


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise SceneConfigError(f"{field} must be true or false")
    return value


def _duration(step: Mapping[str, Any], maximum: float = 30.0) -> float:
    if "duration" in step and "duration_ms" in step:
        raise SceneConfigError("use duration or duration_ms, not both")
    if "duration_ms" in step:
        return _number(step["duration_ms"], "duration_ms", 0.0, maximum * 1000.0) / 1000.0
    return _number(step.get("duration", 0.0), "duration", 0.0, maximum)


def _sound_name(value: Any) -> str:
    name = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or ".." in path.parts
        or path.suffix.lower() != ".wav"
    ):
        raise SceneConfigError("sound file must be a relative .wav path without '..'")
    return path.as_posix()


def _validate_step(raw: Any, location: str) -> SceneStep:
    if not isinstance(raw, Mapping):
        raise SceneConfigError(f"{location} must be an object")
    try:
        action = SceneAction(str(raw.get("action", "")).strip().lower())
    except ValueError as error:
        supported = ", ".join(item.value for item in SceneAction)
        raise SceneConfigError(f"{location}.action must be one of: {supported}") from error

    parameters: dict[str, Any]
    if action is SceneAction.SPEAK:
        text = str(raw.get("text", "")).strip()
        if not text:
            raise SceneConfigError(f"{location}.text cannot be empty")
        if len(text) > 500:
            raise SceneConfigError(f"{location}.text cannot exceed 500 characters")
        parameters = {"text": text}
    elif action is SceneAction.PAUSE:
        parameters = {"duration": _duration(raw)}
        if parameters["duration"] <= 0:
            raise SceneConfigError(f"{location}.duration must be greater than zero")
    elif action is SceneAction.EYES:
        parameters = {
            "level": _number(raw.get("level"), f"{location}.level", 0.0, 1.0),
            "duration": _duration(raw),
        }
    elif action is SceneAction.BLINK:
        low = _number(raw.get("low", 0.0), f"{location}.low", 0.0, 1.0)
        high = _number(raw.get("high", 1.0), f"{location}.high", 0.0, 1.0)
        if high < low:
            raise SceneConfigError(f"{location}.high cannot be lower than low")
        parameters = {
            "count": _integer(raw.get("count", 1), f"{location}.count", 1, 50),
            "period": _number(
                raw.get("period_ms", 160), f"{location}.period_ms", 20.0, 5000.0
            ) / 1000.0,
            "low": low,
            "high": high,
        }
    elif action is SceneAction.FLICKER:
        base = _number(raw.get("base", 0.1), f"{location}.base", 0.0, 1.0)
        span = _number(raw.get("span", 0.8), f"{location}.span", 0.0, 1.0)
        if base + span > 1.0:
            raise SceneConfigError(f"{location}.base plus span cannot exceed 1")
        duration = _duration(raw)
        if duration <= 0:
            raise SceneConfigError(f"{location}.duration must be greater than zero")
        parameters = {
            "duration": duration,
            "base": base,
            "span": span,
            "step": _number(
                raw.get("step_ms", 60), f"{location}.step_ms", 20.0, 2000.0
            ) / 1000.0,
        }
    elif action is SceneAction.JAW:
        duration = _duration(raw)
        if duration <= 0:
            raise SceneConfigError(f"{location}.duration must be greater than zero")
        parameters = {
            "level": _number(raw.get("level"), f"{location}.level", 0.0, 1.0),
            "duration": duration,
        }
    else:
        parameters = {
            "file": _sound_name(raw.get("file")),
            "jaw": _boolean(raw.get("jaw", False), f"{location}.jaw"),
            "volume": _number(
                raw.get("volume", 1.0), f"{location}.volume", 0.0, 2.0
            ),
        }
    return SceneStep(action=action, parameters=parameters)


class SceneLibrary:
    """Immutable collection loaded from one JSON scene file."""

    def __init__(self, scenes: Iterable[Scene]) -> None:
        values = tuple(scenes)
        self._scenes = {scene.name: scene for scene in values}
        if len(self._scenes) != len(values):
            raise SceneConfigError("scene names must be unique")

    @classmethod
    def from_data(
        cls,
        data: Any,
        maximum_scenes: int = 32,
        maximum_steps: int = 64,
    ) -> "SceneLibrary":
        if not isinstance(data, Mapping):
            raise SceneConfigError("scene file must contain a JSON object")
        raw_scenes = data.get("scenes", data)
        if not isinstance(raw_scenes, Mapping):
            raise SceneConfigError("scenes must be an object keyed by scene name")
        if len(raw_scenes) > maximum_scenes:
            raise SceneConfigError(f"scene file cannot exceed {maximum_scenes} scenes")

        scenes = []
        for raw_name, raw_scene in raw_scenes.items():
            name = str(raw_name).strip().lower()
            if not _SCENE_NAME.fullmatch(name):
                raise SceneConfigError(
                    f"invalid scene name {raw_name!r}; use lowercase letters, numbers, '-' or '_'"
                )
            if not isinstance(raw_scene, Mapping):
                raise SceneConfigError(f"scene {name!r} must be an object")
            raw_steps = raw_scene.get("steps")
            if not isinstance(raw_steps, list) or not raw_steps:
                raise SceneConfigError(f"scene {name!r} must have a non-empty steps list")
            if len(raw_steps) > maximum_steps:
                raise SceneConfigError(
                    f"scene {name!r} cannot exceed {maximum_steps} steps"
                )
            steps = tuple(
                _validate_step(step, f"{name}.steps[{index}]")
                for index, step in enumerate(raw_steps)
            )
            scenes.append(
                Scene(
                    name=name,
                    description=str(raw_scene.get("description", "")).strip()[:200],
                    steps=steps,
                )
            )
        return cls(scenes)

    @classmethod
    def load(cls, path: Any, **kwargs: Any) -> "SceneLibrary":
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise SceneConfigError(f"cannot load scene file: {error}") from error
        return cls.from_data(data, **kwargs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._scenes)

    @property
    def referenced_sounds(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            str(step.parameters["file"])
            for scene in self._scenes.values()
            for step in scene.steps
            if step.action is SceneAction.SOUND
        ))

    def get(self, name: Any) -> Optional[Scene]:
        return self._scenes.get(str(name or "").strip().lower())

    def __len__(self) -> int:
        return len(self._scenes)


def resolve_sound_path(root: Any, relative_name: Any) -> Path:
    """Resolve one already-validated cue while enforcing the sound root."""

    root_path = Path(root).resolve()
    relative = _sound_name(relative_name)
    candidate = (root_path / relative).resolve()
    if candidate != root_path and root_path not in candidate.parents:
        raise SceneConfigError("sound file escapes the configured sound directory")
    return candidate


def load_wav_pcm16(
    path: Any,
    target_rate: int,
    maximum_seconds: float = 30.0,
) -> bytes:
    """Load a PCM WAV, mix it to mono, and resample for the warm output stream."""

    target_rate = max(1, int(target_rate))
    try:
        with wave.open(str(path), "rb") as handle:
            channels = handle.getnchannels()
            source_rate = handle.getframerate()
            sample_width = handle.getsampwidth()
            frame_count = handle.getnframes()
            compression = handle.getcomptype()
            if sample_width != 2 or channels < 1 or compression != "NONE":
                raise SceneConfigError("sound cues must be uncompressed 16-bit PCM WAV files")
            if frame_count <= 0:
                raise SceneConfigError("sound cue cannot be empty")
            if source_rate <= 0 or frame_count / float(source_rate) > maximum_seconds:
                raise SceneConfigError(
                    f"sound cue cannot exceed {maximum_seconds:g} seconds"
                )
            raw = handle.readframes(frame_count)
    except (OSError, EOFError, wave.Error) as error:
        raise SceneConfigError(f"cannot read sound cue: {error}") from error

    samples = np.frombuffer(raw, dtype="<i2")
    if channels > 1:
        usable = samples.size - (samples.size % channels)
        samples = np.round(
            samples[:usable].reshape(-1, channels).astype(np.float32).mean(axis=1)
        ).astype(np.int16)
    else:
        samples = samples.astype(np.int16, copy=False)
    if source_rate == target_rate or samples.size < 2:
        return samples.tobytes()

    target_count = max(1, int(round(samples.size * target_rate / float(source_rate))))
    positions = np.arange(target_count, dtype=np.float64) * source_rate / float(target_rate)
    positions = np.minimum(positions, samples.size - 1)
    resampled = np.interp(
        positions,
        np.arange(samples.size, dtype=np.float64),
        samples.astype(np.float64),
    )
    return np.clip(np.round(resampled), -32768, 32767).astype(np.int16).tobytes()


class _SceneStopSignal:
    """Threading-event interface combining an external interrupt and deadline."""

    def __init__(
        self,
        interrupt_event: Any,
        deadline: float,
        clock: Callable[[], float],
    ) -> None:
        self.interrupt_event = interrupt_event
        self.deadline = deadline
        self.clock = clock

    @property
    def timed_out(self) -> bool:
        return self.clock() >= self.deadline

    def is_set(self) -> bool:
        return bool(self.interrupt_event.is_set() or self.timed_out)

    def wait(self, timeout: Optional[float] = None) -> bool:
        remaining = max(0.0, self.deadline - self.clock())
        duration = remaining if timeout is None else min(max(0.0, timeout), remaining)
        if duration > 0 and self.interrupt_event.wait(duration):
            return True
        return self.is_set()


SceneExecutor = Callable[[SceneStep, Any], bool]
SceneProgress = Callable[[Scene, int, SceneStep], None]


class SceneRunner:
    """Run one bounded scene on its caller's serialized hardware thread."""

    def __init__(
        self,
        executor: SceneExecutor,
        maximum_seconds: float = 30.0,
        progress: Optional[SceneProgress] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.executor = executor
        self.maximum_seconds = max(0.1, float(maximum_seconds))
        self.progress = progress
        self.clock = clock

    def run(self, scene: Scene, interrupt_event: Any) -> SceneResult:
        started_at = self.clock()
        stop_signal = _SceneStopSignal(
            interrupt_event,
            started_at + self.maximum_seconds,
            self.clock,
        )
        completed = 0
        error = ""
        interrupted = False
        try:
            for index, step in enumerate(scene.steps, start=1):
                if stop_signal.is_set():
                    interrupted = True
                    break
                if self.progress is not None:
                    self.progress(scene, index, step)
                if step.action is SceneAction.PAUSE:
                    interrupted = stop_signal.wait(float(step.parameters["duration"]))
                else:
                    interrupted = bool(self.executor(step, stop_signal))
                if interrupted or stop_signal.is_set():
                    interrupted = True
                    break
                completed += 1
        except Exception as caught:
            error = str(caught)

        timed_out = stop_signal.timed_out
        return SceneResult(
            scene_name=scene.name,
            completed_steps=completed,
            total_steps=len(scene.steps),
            duration_seconds=self.clock() - started_at,
            interrupted=interrupted or timed_out,
            timed_out=timed_out,
            error=error,
        )
