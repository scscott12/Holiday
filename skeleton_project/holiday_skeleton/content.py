"""Transactional preparation for live personality and scene reloads."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Optional

from .personality import PersonalityConfigError, PersonalityLibrary, PersonalityPack
from .scene import (
    SceneAction,
    SceneConfigError,
    SceneLibrary,
    load_wav_pcm16,
    resolve_sound_path,
)


class ContentReloadError(ValueError):
    """Candidate content cannot safely replace the active libraries."""


class ContentReloadInterrupted(RuntimeError):
    """Foreground work arrived before the candidate could be committed."""


@dataclass(frozen=True)
class PreparedContent:
    """Fully validated immutable content waiting for one runtime swap."""

    personalities: Optional[PersonalityLibrary]
    active_personality: Optional[PersonalityPack]
    scenes: Optional[SceneLibrary]
    sound_cache: Mapping[str, bytes]
    canned_lines: tuple[str, ...]


def _checkpoint(interrupted: Callable[[], bool]) -> None:
    if interrupted():
        raise ContentReloadInterrupted("reload interrupted by foreground activity")


def _bounded_file(path: str, label: str, maximum_bytes: int) -> None:
    candidate = Path(path)
    try:
        size = candidate.stat().st_size
    except OSError as error:
        raise ContentReloadError(f"cannot inspect {label}: {error}") from error
    if not candidate.is_file():
        raise ContentReloadError(f"{label} is not a regular file")
    if size > maximum_bytes:
        raise ContentReloadError(
            f"{label} cannot exceed {maximum_bytes // 1024} KiB"
        )


def _canned_lines(
    pack: Optional[PersonalityPack],
    scenes: Optional[SceneLibrary],
    additional_lines: tuple[str, ...],
) -> tuple[str, ...]:
    lines = list(pack.canned_lines if pack is not None else ())
    if scenes is not None:
        lines.extend(
            str(step.parameters["text"])
            for scene_name in scenes.names
            for step in scenes.get(scene_name).steps
            if step.action is SceneAction.SPEAK
        )
    lines.extend(additional_lines)
    return tuple(dict.fromkeys(
        text
        for text in (" ".join(str(line or "").split()) for line in lines)
        if text
    ))


def prepare_content(
    *,
    personalities_enabled: bool,
    personalities_path: str,
    requested_personality: str,
    current_personality: str,
    scenes_enabled: bool,
    scenes_path: str,
    sound_directory: str,
    sound_sample_rate: int,
    scene_maximum_seconds: float,
    cache_sounds: bool,
    maximum_json_bytes: int = 1024 * 1024,
    maximum_sound_bytes: int = 64 * 1024 * 1024,
    maximum_canned_lines: int = 256,
    maximum_canned_characters: int = 64 * 1024,
    additional_canned_lines: tuple[str, ...] = (),
    interrupted: Callable[[], bool] = lambda: False,
) -> PreparedContent:
    """Load and validate a complete replacement without mutating live state.

    The current named personality must remain available. This prevents an
    innocent file edit from silently changing the character that is active in
    front of visitors. Every default-scene link and referenced WAV is checked
    before the caller receives a bundle it can commit.
    """

    try:
        _checkpoint(interrupted)
        if personalities_enabled:
            _bounded_file(
                personalities_path,
                "personality file",
                max(1, int(maximum_json_bytes)),
            )
        personalities = (
            PersonalityLibrary.load(personalities_path)
            if personalities_enabled
            else None
        )
        _checkpoint(interrupted)
        if scenes_enabled:
            _bounded_file(
                scenes_path,
                "scene file",
                max(1, int(maximum_json_bytes)),
            )
        scenes = SceneLibrary.load(scenes_path) if scenes_enabled else None
        _checkpoint(interrupted)

        active: Optional[PersonalityPack] = None
        if personalities is not None:
            current = str(current_personality or "").strip().lower()
            selection = (
                current
                if current and current != "legacy"
                else str(requested_personality or "").strip().lower() or None
            )
            active = personalities.select(selection)

        if personalities is not None and scenes is not None:
            cross_reference_errors = personalities.validate_scenes(scenes.names)
            if cross_reference_errors:
                raise ContentReloadError("; ".join(cross_reference_errors))

        sound_cache: dict[str, bytes] = {}
        sound_bytes = 0
        sound_limit = max(1, int(maximum_sound_bytes))
        if scenes is not None:
            for name in scenes.referenced_sounds:
                _checkpoint(interrupted)
                path = resolve_sound_path(sound_directory, name)
                if not path.is_file():
                    raise ContentReloadError(f"sound cue not found: {name}")
                pcm = load_wav_pcm16(
                    path,
                    target_rate=sound_sample_rate,
                    maximum_seconds=scene_maximum_seconds,
                )
                sound_bytes += len(pcm)
                if sound_bytes > sound_limit:
                    raise ContentReloadError(
                        f"decoded scene sounds exceed the {sound_limit} byte reload limit"
                    )
                if cache_sounds:
                    sound_cache[name] = pcm

        _checkpoint(interrupted)
        lines = _canned_lines(active, scenes, additional_canned_lines)
        line_limit = max(1, int(maximum_canned_lines))
        if len(lines) > line_limit:
            raise ContentReloadError(
                f"content reload cannot cache more than {line_limit} lines"
            )
        character_count = sum(len(line) for line in lines)
        if character_count > max(1, int(maximum_canned_characters)):
            raise ContentReloadError(
                "content reload canned speech exceeds the character budget"
            )
        return PreparedContent(
            personalities=personalities,
            active_personality=active,
            scenes=scenes,
            sound_cache=MappingProxyType(sound_cache),
            canned_lines=lines,
        )
    except ContentReloadInterrupted:
        raise
    except ContentReloadError:
        raise
    except (PersonalityConfigError, SceneConfigError) as error:
        raise ContentReloadError(str(error)) from error
    except OSError as error:
        raise ContentReloadError(f"cannot prepare content: {error}") from error
