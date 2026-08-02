#!/usr/bin/env python3
"""Validate repository-owned content without Raspberry Pi hardware."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys
from typing import Any

import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from holiday_skeleton.content import ContentReloadError, prepare_content
from holiday_skeleton.discovery import discovery_messages


class ProjectValidationError(ValueError):
    """A packaged file cannot safely be shipped."""


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _validate_yaml(path: Path) -> None:
    try:
        with path.open("r", encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as error:
        relative_path = path.relative_to(PROJECT_ROOT)
        raise ProjectValidationError(
            f"cannot parse {relative_path}: {error}"
        ) from error
    if not isinstance(document, (dict, list)) or not document:
        raise ProjectValidationError(
            f"{path.relative_to(PROJECT_ROOT)} must contain a non-empty YAML mapping or list"
        )


def _validate_discovery(personality_names: tuple[str, ...]) -> int:
    messages = discovery_messages("skeleton", personality_names)
    topics = [topic for topic, _payload in messages]
    duplicate_topics = _duplicates(topics)
    if duplicate_topics:
        raise ProjectValidationError(
            "duplicate Home Assistant discovery topics: " + ", ".join(duplicate_topics)
        )

    unique_ids: list[str] = []
    for topic, payload in messages:
        if payload is None:
            continue
        if not isinstance(payload, dict):
            raise ProjectValidationError(
                f"discovery payload for {topic} is not an object"
            )
        unique_id = payload.get("uniq_id")
        if not isinstance(unique_id, str) or not unique_id.strip():
            raise ProjectValidationError(f"discovery payload for {topic} has no uniq_id")
        unique_ids.append(unique_id)
        try:
            json.dumps(payload, allow_nan=False, sort_keys=True)
        except (TypeError, ValueError) as error:
            raise ProjectValidationError(
                f"discovery payload for {topic} is not strict JSON: {error}"
            ) from error

    duplicate_ids = _duplicates(unique_ids)
    if duplicate_ids:
        raise ProjectValidationError(
            "duplicate Home Assistant discovery uniq_id values: " + ", ".join(duplicate_ids)
        )
    return len(topics)


def validate_project(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    prepared = prepare_content(
        personalities_enabled=True,
        personalities_path=str(root / "personalities.json"),
        requested_personality="",
        current_personality="",
        scenes_enabled=True,
        scenes_path=str(root / "scenes.json"),
        sound_directory=str(root / "sounds"),
        sound_sample_rate=22_050,
        scene_maximum_seconds=30.0,
        cache_sounds=False,
    )
    if prepared.personalities is None or prepared.scenes is None:
        raise ProjectValidationError(
            "packaged personality and scene libraries must be enabled"
        )

    yaml_paths = sorted((root / "ha").glob("*.yaml"))
    if not yaml_paths:
        raise ProjectValidationError("no Home Assistant YAML files found")
    for path in yaml_paths:
        _validate_yaml(path)

    discovery_count = _validate_discovery(prepared.personalities.names)
    return {
        "personalities": len(prepared.personalities),
        "scenes": len(prepared.scenes),
        "yaml_files": len(yaml_paths),
        "discovery_topics": discovery_count,
    }


def main() -> int:
    try:
        summary = validate_project()
    except (ContentReloadError, ProjectValidationError) as error:
        print(f"Project validation failed: {error}", file=sys.stderr)
        return 1
    print(
        "Project validation passed: "
        f"{summary['personalities']} personalities, "
        f"{summary['scenes']} scenes, "
        f"{summary['yaml_files']} YAML files, "
        f"{summary['discovery_topics']} unique discovery topics"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
