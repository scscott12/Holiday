"""Validated, immutable personality packs for the skeleton runtime."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional


class PersonalityConfigError(ValueError):
    """Raised when a personality library is malformed or unsafe."""


_PACK_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_PERIODS = ("morning", "afternoon", "evening", "night")


def _reject_unknown(raw: Mapping[str, Any], allowed: set[str], field: str) -> None:
    unknown = set(raw) - allowed
    if unknown:
        raise PersonalityConfigError(
            f"{field} has unknown fields: {', '.join(sorted(map(str, unknown)))}"
        )


def can_switch_personality(runtime_state: Any) -> bool:
    """Allow changes only outside visits, scenes, speech, and idle actions."""

    value = getattr(runtime_state, "value", runtime_state)
    return str(value or "").strip().lower() in ("idle", "cooldown")


def _number(
    value: Any,
    field: str,
    minimum: float,
    maximum: float,
    *,
    integer: bool = False,
) -> float | int:
    if isinstance(value, bool):
        raise PersonalityConfigError(f"{field} must be a number")
    try:
        result = int(value) if integer else float(value)
    except (TypeError, ValueError) as error:
        raise PersonalityConfigError(f"{field} must be a number") from error
    if integer and float(value) != result:
        raise PersonalityConfigError(f"{field} must be a whole number")
    if result < minimum or result > maximum:
        raise PersonalityConfigError(
            f"{field} must be between {minimum:g} and {maximum:g}"
        )
    return result


def _boolean(value: Any, field: str) -> bool:
    if not isinstance(value, bool):
        raise PersonalityConfigError(f"{field} must be true or false")
    return value


def _text(value: Any, field: str, maximum: int, *, required: bool = True) -> str:
    if not isinstance(value, str):
        raise PersonalityConfigError(f"{field} must be text")
    result = " ".join(value.split())
    if required and not result:
        raise PersonalityConfigError(f"{field} cannot be empty")
    if len(result) > maximum:
        raise PersonalityConfigError(f"{field} cannot exceed {maximum} characters")
    return result


def _lines(value: Any, field: str, *, maximum_lines: int = 12) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PersonalityConfigError(f"{field} must be a non-empty list")
    if len(value) > maximum_lines:
        raise PersonalityConfigError(f"{field} cannot exceed {maximum_lines} lines")
    result = tuple(_text(line, f"{field}[{index}]", 300) for index, line in enumerate(value))
    if len(set(result)) != len(result):
        raise PersonalityConfigError(f"{field} cannot contain duplicate lines")
    return result


def _words(value: Any, field: str, *, maximum_words: int = 12) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise PersonalityConfigError(f"{field} must be a non-empty list")
    if len(value) > maximum_words:
        raise PersonalityConfigError(f"{field} cannot exceed {maximum_words} entries")
    result = tuple(
        _text(word, f"{field}[{index}]", 40).lower()
        for index, word in enumerate(value)
    )
    if len(set(result)) != len(result):
        raise PersonalityConfigError(f"{field} cannot contain duplicates")
    return result


@dataclass(frozen=True)
class ReplySettings:
    memory_turns: int
    context_tokens: int
    maximum_tokens: int
    temperature: float
    repeat_penalty: float
    phrase_minimum: int
    phrase_soft: int
    phrase_maximum: int


@dataclass(frozen=True)
class VoiceSettings:
    volume_multiplier: float


@dataclass(frozen=True)
class BargeInSettings:
    stop_commands: tuple[str, ...]
    listen_commands: tuple[str, ...]
    wake_words: tuple[str, ...]
    require_wake_word: bool


@dataclass(frozen=True)
class PersonalityPack:
    name: str
    display_name: str
    description: str
    system_prompt: str
    opening_lines: Mapping[str, tuple[str, ...]]
    goodbye_lines: tuple[str, ...]
    idle_lines: tuple[str, ...]
    fallback_line: str
    reply: ReplySettings
    voice: VoiceSettings
    barge_in: BargeInSettings
    default_scene: str

    @property
    def canned_lines(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            line
            for group in (
                *(self.opening_lines[period] for period in _PERIODS),
                self.goodbye_lines,
                self.idle_lines,
            )
            for line in group
        ))

    def metadata(self) -> dict[str, Any]:
        return {
            "display_name": self.display_name,
            "description": self.description,
            "default_scene": self.default_scene or None,
            "wake_words": list(self.barge_in.wake_words),
            "memory_turns": self.reply.memory_turns,
            "context_tokens": self.reply.context_tokens,
            "volume_multiplier": self.voice.volume_multiplier,
        }


def _parse_pack(name: str, raw: Any) -> PersonalityPack:
    if not isinstance(raw, Mapping):
        raise PersonalityConfigError(f"personality {name!r} must be an object")
    _reject_unknown(raw, {
        "display_name", "description", "system_prompt", "opening_lines",
        "goodbye_lines", "idle_lines", "fallback_line", "reply", "voice",
        "barge_in", "default_scene",
    }, name)

    opening = raw.get("opening_lines")
    if not isinstance(opening, Mapping):
        raise PersonalityConfigError(f"{name}.opening_lines must be an object")
    extra_periods = set(opening) - set(_PERIODS)
    if extra_periods:
        raise PersonalityConfigError(
            f"{name}.opening_lines has unknown periods: {', '.join(sorted(extra_periods))}"
        )
    opening_lines = {
        period: _lines(opening.get(period), f"{name}.opening_lines.{period}")
        for period in _PERIODS
    }

    reply_raw = raw.get("reply", {})
    if not isinstance(reply_raw, Mapping):
        raise PersonalityConfigError(f"{name}.reply must be an object")
    _reject_unknown(reply_raw, {
        "memory_turns", "context_tokens", "maximum_tokens", "temperature",
        "repeat_penalty", "phrase_minimum", "phrase_soft", "phrase_maximum",
    }, f"{name}.reply")
    phrase_minimum = int(_number(reply_raw.get("phrase_minimum", 12), f"{name}.reply.phrase_minimum", 1, 200, integer=True))
    phrase_soft = int(_number(reply_raw.get("phrase_soft", 36), f"{name}.reply.phrase_soft", 1, 300, integer=True))
    phrase_maximum = int(_number(reply_raw.get("phrase_maximum", 72), f"{name}.reply.phrase_maximum", 1, 500, integer=True))
    if not phrase_minimum <= phrase_soft <= phrase_maximum:
        raise PersonalityConfigError(
            f"{name}.reply phrase boundaries must satisfy minimum <= soft <= maximum"
        )
    reply = ReplySettings(
        memory_turns=int(_number(reply_raw.get("memory_turns", 3), f"{name}.reply.memory_turns", 0, 10, integer=True)),
        context_tokens=int(_number(reply_raw.get("context_tokens", 512), f"{name}.reply.context_tokens", 128, 8192, integer=True)),
        maximum_tokens=int(_number(reply_raw.get("maximum_tokens", 50), f"{name}.reply.maximum_tokens", 8, 256, integer=True)),
        temperature=float(_number(reply_raw.get("temperature", 0.6), f"{name}.reply.temperature", 0, 2)),
        repeat_penalty=float(_number(reply_raw.get("repeat_penalty", 1.05), f"{name}.reply.repeat_penalty", 0.5, 2)),
        phrase_minimum=phrase_minimum,
        phrase_soft=phrase_soft,
        phrase_maximum=phrase_maximum,
    )

    voice_raw = raw.get("voice", {})
    if not isinstance(voice_raw, Mapping):
        raise PersonalityConfigError(f"{name}.voice must be an object")
    _reject_unknown(voice_raw, {"volume_multiplier"}, f"{name}.voice")
    voice = VoiceSettings(
        volume_multiplier=float(_number(voice_raw.get("volume_multiplier", 1.0), f"{name}.voice.volume_multiplier", 0.25, 2.0))
    )

    barge_raw = raw.get("barge_in", {})
    if not isinstance(barge_raw, Mapping):
        raise PersonalityConfigError(f"{name}.barge_in must be an object")
    _reject_unknown(barge_raw, {
        "stop_commands", "listen_commands", "wake_words", "require_wake_word",
    }, f"{name}.barge_in")
    barge_in = BargeInSettings(
        stop_commands=_words(barge_raw.get("stop_commands", ["stop", "quiet"]), f"{name}.barge_in.stop_commands"),
        listen_commands=_words(barge_raw.get("listen_commands", ["wait"]), f"{name}.barge_in.listen_commands"),
        wake_words=_words(barge_raw.get("wake_words", ["skeleton"]), f"{name}.barge_in.wake_words"),
        require_wake_word=_boolean(barge_raw.get("require_wake_word", False), f"{name}.barge_in.require_wake_word"),
    )

    default_scene = _text(raw.get("default_scene", ""), f"{name}.default_scene", 64, required=False).lower()
    if default_scene and not _PACK_NAME.fullmatch(default_scene):
        raise PersonalityConfigError(f"{name}.default_scene is not a safe scene name")

    return PersonalityPack(
        name=name,
        display_name=_text(raw.get("display_name", name.replace("_", " ").title()), f"{name}.display_name", 80),
        description=_text(raw.get("description", ""), f"{name}.description", 240, required=False),
        system_prompt=_text(raw.get("system_prompt"), f"{name}.system_prompt", 1600),
        opening_lines=MappingProxyType(opening_lines),
        goodbye_lines=_lines(raw.get("goodbye_lines"), f"{name}.goodbye_lines"),
        idle_lines=_lines(raw.get("idle_lines"), f"{name}.idle_lines"),
        fallback_line=_text(raw.get("fallback_line", "Say that again."), f"{name}.fallback_line", 300),
        reply=reply,
        voice=voice,
        barge_in=barge_in,
        default_scene=default_scene,
    )


class PersonalityLibrary:
    """Immutable collection of bounded personality packs."""

    def __init__(self, packs: Iterable[PersonalityPack], default_name: str) -> None:
        values = tuple(packs)
        packs_by_name = {pack.name: pack for pack in values}
        if len(packs_by_name) != len(values):
            raise PersonalityConfigError("personality names must be unique")
        if not packs_by_name:
            raise PersonalityConfigError("at least one personality is required")
        if default_name not in packs_by_name:
            raise PersonalityConfigError(f"active personality {default_name!r} does not exist")
        self._packs = MappingProxyType(packs_by_name)
        self.default_name = default_name

    @classmethod
    def from_data(cls, data: Any, maximum_packs: int = 12) -> "PersonalityLibrary":
        if not isinstance(data, Mapping):
            raise PersonalityConfigError("personality file must contain a JSON object")
        _reject_unknown(data, {"version", "active", "personalities"}, "personality file")
        _number(data.get("version", 1), "personality file.version", 1, 1, integer=True)
        raw_packs = data.get("personalities")
        if not isinstance(raw_packs, Mapping) or not raw_packs:
            raise PersonalityConfigError("personalities must be a non-empty object")
        if len(raw_packs) > maximum_packs:
            raise PersonalityConfigError(
                f"personality file cannot exceed {maximum_packs} packs"
            )

        packs = []
        for raw_name, raw_pack in raw_packs.items():
            name = str(raw_name).strip().lower()
            if not _PACK_NAME.fullmatch(name):
                raise PersonalityConfigError(
                    f"invalid personality name {raw_name!r}; use lowercase letters, numbers, '-' or '_'"
                )
            packs.append(_parse_pack(name, raw_pack))

        default_name = str(data.get("active", packs[0].name)).strip().lower()
        return cls(packs, default_name)

    @classmethod
    def load(cls, path: Any, **kwargs: Any) -> "PersonalityLibrary":
        try:
            with Path(path).open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as error:
            raise PersonalityConfigError(f"cannot load personality file: {error}") from error
        return cls.from_data(data, **kwargs)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._packs)

    def get(self, name: Any) -> Optional[PersonalityPack]:
        return self._packs.get(str(name or "").strip().lower())

    def select(self, name: Any = None) -> PersonalityPack:
        requested = self.default_name if name is None or not str(name).strip() else str(name).strip().lower()
        pack = self.get(requested)
        if pack is None:
            available = ", ".join(self.names)
            raise PersonalityConfigError(
                f"unknown personality {requested!r}; available: {available}"
            )
        return pack

    def validate_scenes(self, available_names: Iterable[str]) -> tuple[str, ...]:
        available = {str(name).strip().lower() for name in available_names}
        return tuple(
            f"{pack.name}: unknown default scene {pack.default_scene!r}"
            for pack in self._packs.values()
            if pack.default_scene and pack.default_scene not in available
        )

    def metadata(self) -> dict[str, Any]:
        return {
            "names": list(self.names),
            "active_default": self.default_name,
            "personalities": {
                name: self._packs[name].metadata() for name in self.names
            },
        }

    def __len__(self) -> int:
        return len(self._packs)
