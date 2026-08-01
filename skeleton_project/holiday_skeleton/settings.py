"""Versioned, atomic persistence for non-sensitive operator settings."""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


SETTINGS_VERSION = 2
MAX_SETTINGS_BYTES = 16 * 1024
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SettingsConfigError(ValueError):
    """Raised when saved operator settings are unsafe or incompatible."""


@dataclass(frozen=True)
class DayProfile:
    eyes_dim: float
    eyes_full: float
    volume: float


@dataclass(frozen=True)
class OperatorSettings:
    personality: str
    motion_enabled: bool
    idle_life_enabled: bool
    night_mode: bool
    eyes_dim: float
    eyes_full: float
    volume: float
    day_profile: DayProfile
    maintenance_mode: bool = False
    updated_at: str = "never"

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": SETTINGS_VERSION,
            "updated_at": self.updated_at,
            "settings": {
                "personality": self.personality,
                "motion_enabled": self.motion_enabled,
                "idle_life_enabled": self.idle_life_enabled,
                "night_mode": self.night_mode,
                "maintenance_mode": self.maintenance_mode,
                "eyes_dim": self.eyes_dim,
                "eyes_full": self.eyes_full,
                "volume": self.volume,
                "day_profile": {
                    "eyes_dim": self.day_profile.eyes_dim,
                    "eyes_full": self.day_profile.eyes_full,
                    "volume": self.day_profile.volume,
                },
            },
        }


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise SettingsConfigError(f"{label} must be an object")
    return value


def _require_exact_fields(
    value: Mapping[str, Any],
    expected: set[str],
    label: str,
) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise SettingsConfigError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise SettingsConfigError(f"{label} is missing fields: {', '.join(missing)}")


def _boolean(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsConfigError(f"{label} must be true or false")
    return value


def _number(value: Any, label: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SettingsConfigError(f"{label} must be a number")
    number = float(value)
    if not math.isfinite(number) or not minimum <= number <= maximum:
        raise SettingsConfigError(
            f"{label} must be between {minimum:g} and {maximum:g}"
        )
    return number


def settings_from_payload(payload: Any) -> OperatorSettings:
    root = _mapping(payload, "saved settings")
    _require_exact_fields(root, {"version", "updated_at", "settings"}, "saved settings")
    version = root["version"]
    if isinstance(version, bool) or version not in (1, SETTINGS_VERSION):
        raise SettingsConfigError(
            f"unsupported settings version {version!r}; expected 1 or {SETTINGS_VERSION}"
        )
    updated_at = root["updated_at"]
    if not isinstance(updated_at, str) or not updated_at or len(updated_at) > 64:
        raise SettingsConfigError("updated_at must be a short timestamp")

    values = _mapping(root["settings"], "settings")
    expected = {
        "personality",
        "motion_enabled",
        "idle_life_enabled",
        "night_mode",
        "eyes_dim",
        "eyes_full",
        "volume",
        "day_profile",
    }
    if version >= 2:
        expected.add("maintenance_mode")
    _require_exact_fields(values, expected, "settings")
    personality = values["personality"]
    if not isinstance(personality, str) or not _SAFE_NAME.fullmatch(personality):
        raise SettingsConfigError("personality must be a safe lowercase name")

    day_values = _mapping(values["day_profile"], "day_profile")
    _require_exact_fields(
        day_values,
        {"eyes_dim", "eyes_full", "volume"},
        "day_profile",
    )
    day_profile = DayProfile(
        eyes_dim=_number(day_values["eyes_dim"], "day_profile.eyes_dim", 0.0, 1.0),
        eyes_full=_number(day_values["eyes_full"], "day_profile.eyes_full", 0.0, 1.0),
        volume=_number(day_values["volume"], "day_profile.volume", 0.0, 2.0),
    )
    return OperatorSettings(
        personality=personality,
        motion_enabled=_boolean(values["motion_enabled"], "motion_enabled"),
        idle_life_enabled=_boolean(values["idle_life_enabled"], "idle_life_enabled"),
        night_mode=_boolean(values["night_mode"], "night_mode"),
        eyes_dim=_number(values["eyes_dim"], "eyes_dim", 0.0, 1.0),
        eyes_full=_number(values["eyes_full"], "eyes_full", 0.0, 1.0),
        volume=_number(values["volume"], "volume", 0.0, 2.0),
        day_profile=day_profile,
        maintenance_mode=(
            _boolean(values["maintenance_mode"], "maintenance_mode")
            if version >= 2
            else False
        ),
        updated_at=updated_at,
    )


class OperatorSettingsStore:
    """Load and atomically replace one bounded JSON settings document."""

    def __init__(self, path: os.PathLike[str] | str) -> None:
        self.path = Path(path)

    def load(self) -> Optional[OperatorSettings]:
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(MAX_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as error:
            raise SettingsConfigError(f"cannot read saved settings: {error}") from error
        if len(raw) > MAX_SETTINGS_BYTES:
            raise SettingsConfigError(
                f"saved settings exceed {MAX_SETTINGS_BYTES} bytes"
            )
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise SettingsConfigError(f"cannot parse saved settings: {error}") from error
        return settings_from_payload(payload)

    def save(self, settings: OperatorSettings) -> OperatorSettings:
        updated = replace(
            settings,
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        encoded = (
            json.dumps(updated.to_payload(), indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
        if len(encoded) > MAX_SETTINGS_BYTES:
            raise SettingsConfigError(
                f"saved settings exceed {MAX_SETTINGS_BYTES} bytes"
            )

        parent = self.path.parent
        temporary: Optional[Path] = None
        try:
            parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=str(parent),
                prefix=f".{self.path.name}.",
                suffix=".tmp",
            )
            temporary = Path(temporary_name)
            try:
                os.fchmod(descriptor, 0o600)
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
                raise
            os.replace(temporary, self.path)
            temporary = None
            os.chmod(self.path, 0o600)
            try:
                directory_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
            return updated
        except (OSError, SettingsConfigError) as error:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass
            if isinstance(error, SettingsConfigError):
                raise
            raise SettingsConfigError(f"cannot save operator settings: {error}") from error
