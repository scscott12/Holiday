#!/usr/bin/env python3
"""Install or roll back one verified Holiday Skeleton release on a Raspberry Pi."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import subprocess
import sys
from typing import Iterator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from holiday_skeleton.deployment import (
    DeploymentError,
    DeploymentPaths,
    ReleaseDeployer,
)


DEFAULT_LOCK = Path("/run/lock/holiday-skeleton-deploy.lock")


def _source_commit(source: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(source), "rev-parse", "--short=12", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        value = completed.stdout.strip().lower()
        if value and all(character in "0123456789abcdef" for character in value):
            return value
    except (OSError, subprocess.SubprocessError):
        pass
    return "package"


def default_release_id(source: Path) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{_source_commit(source)}"


@contextmanager
def deployment_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise DeploymentError("another skeleton deployment is already running") from error
        yield
    finally:
        os.close(descriptor)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description=(
            "Prepare a versioned skeleton runtime, switch it atomically, and "
            "restore the last working release if systemd readiness fails."
        )
    )
    action = result.add_mutually_exclusive_group()
    action.add_argument(
        "--rollback",
        action="store_true",
        help="restore the release, settings, content, and unit saved by the last deployment",
    )
    result.add_argument(
        "--release-id",
        help="safe unique release label; defaults to UTC timestamp plus Git commit",
    )
    result.add_argument("--source", type=Path, default=PROJECT_ROOT)
    result.add_argument("--prefix", type=Path, default=Path("/opt/holiday-skeleton"))
    result.add_argument(
        "--state-directory",
        type=Path,
        default=Path("/var/lib/holiday-skeleton"),
    )
    result.add_argument(
        "--service-unit",
        type=Path,
        default=Path("/etc/systemd/system/holiday-skeleton.service"),
    )
    result.add_argument(
        "--lock-file",
        type=Path,
        default=DEFAULT_LOCK,
        help=argparse.SUPPRESS,
    )
    result.add_argument(
        "--health-timeout",
        type=float,
        default=150.0,
        help="maximum seconds to wait for Type=notify READY=1 (default: 150)",
    )
    result.add_argument(
        "--settle-seconds",
        type=float,
        default=5.0,
        help="active/running stability window after readiness (default: 5)",
    )
    result.add_argument(
        "--minimum-free-mib",
        type=int,
        default=512,
        help="minimum free space required before staging (default: 512)",
    )
    return result


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    if os.geteuid() != 0:
        print(
            "Deployment requires root so it can manage /opt, systemd, and the "
            "private state backup. Run it with sudo.",
            file=sys.stderr,
        )
        return 2
    paths = DeploymentPaths(
        source=options.source,
        prefix=options.prefix,
        state_directory=options.state_directory,
        service_unit=options.service_unit,
    )
    deployer = ReleaseDeployer(
        paths,
        health_timeout=options.health_timeout,
        settle_seconds=options.settle_seconds,
        minimum_free_bytes=max(0, options.minimum_free_mib) * 1024 * 1024,
    )
    try:
        with deployment_lock(options.lock_file):
            if options.rollback:
                result = deployer.rollback_last()
                target = result.previous_release or "legacy pre-release layout"
                print(
                    f"Rollback complete: {result.release_id} -> {target}; "
                    f"restored from {result.backup_path}"
                )
            else:
                release_id = options.release_id or default_release_id(paths.source)
                result = deployer.deploy(release_id)
                previous = result.previous_release or "legacy pre-release layout"
                print(
                    f"Deployment complete: {result.release_id} is active at "
                    f"{result.release_path} (previous: {previous})"
                )
                print(f"Rollback snapshot: {result.backup_path}")
        return 0
    except DeploymentError as error:
        print(f"Deployment failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
