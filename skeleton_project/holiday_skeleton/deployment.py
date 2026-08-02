"""Versioned Raspberry Pi deployment with verified automatic rollback."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional


RELEASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RELEASE_FILES = (
    "skeleton_all_in_one_mqtt.py",
    "requirements.txt",
    "personalities.json",
    "scenes.json",
    "holiday_skeleton",
    "systemd",
)
SHARED_CONTENT = ("personalities.json", "scenes.json", "sounds")
STATE_FILES = ("operator-settings.json", "diagnostic-events.json")
MAX_CONTENT_FILES = 256
MAX_CONTENT_BYTES = 128 * 1024 * 1024
MAX_STATE_FILE_BYTES = 1024 * 1024
DEPLOYMENT_RECORD_VERSION = 1


class DeploymentError(RuntimeError):
    """A release could not be prepared, activated, or rolled back safely."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def validate_release_id(value: str) -> str:
    release_id = str(value or "").strip()
    if (
        not RELEASE_ID_PATTERN.fullmatch(release_id)
        or release_id in (".", "..")
        or ".." in release_id
        or release_id.startswith("staging-")
    ):
        raise DeploymentError(
            "release id must be 1-64 safe letters, numbers, dots, dashes, or underscores"
        )
    return release_id


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _tree_files(root: Path) -> list[Path]:
    files: list[Path] = []
    if not root.exists():
        return files
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise DeploymentError(f"release content cannot contain symlinks: {path}")
        if path.is_file():
            files.append(path)
        elif not path.is_dir():
            raise DeploymentError(f"release content has an unsupported entry: {path}")
    return files


def _bounded_content(paths: Iterable[Path]) -> None:
    file_count = 0
    total_bytes = 0
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise DeploymentError(f"shared content must be a regular file: {path}")
        file_count += 1
        total_bytes += path.stat().st_size
        if file_count > MAX_CONTENT_FILES:
            raise DeploymentError(
                f"shared content cannot exceed {MAX_CONTENT_FILES} files"
            )
        if total_bytes > MAX_CONTENT_BYTES:
            raise DeploymentError(
                f"shared content cannot exceed {MAX_CONTENT_BYTES // (1024 * 1024)} MiB"
            )


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Optional[Path] = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        os.chmod(path, mode)
    except OSError as error:
        raise DeploymentError(f"cannot atomically write {path}: {error}") from error
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _atomic_json(path: Path, payload: dict[str, Any], mode: int = 0o600) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    _atomic_write(path, encoded, mode)


@dataclass(frozen=True)
class DeploymentPaths:
    source: Path
    prefix: Path = Path("/opt/holiday-skeleton")
    state_directory: Path = Path("/var/lib/holiday-skeleton")
    service_unit: Path = Path("/etc/systemd/system/holiday-skeleton.service")

    @property
    def releases(self) -> Path:
        return self.prefix / "releases"

    @property
    def current(self) -> Path:
        return self.prefix / "current"

    @property
    def backups(self) -> Path:
        return self.deployment_state / "backups"

    @property
    def record(self) -> Path:
        return self.deployment_state / "last-deployment.json"

    @property
    def deployment_state(self) -> Path:
        return self.state_directory.parent / f"{self.state_directory.name}-deploy"


@dataclass(frozen=True)
class DeploymentResult:
    release_id: str
    release_path: Path
    previous_release: Optional[Path]
    backup_path: Path
    rolled_back: bool = False


class SystemdManager:
    """Small command boundary used by the real deployer and hardware-free tests."""

    def __init__(self, service_name: str = "holiday-skeleton") -> None:
        self.service_name = service_name

    @staticmethod
    def command_available(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def run(
        arguments: list[str],
        *,
        timeout: float = 120.0,
        capture: bool = True,
        cwd: Optional[Path] = None,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                arguments,
                check=True,
                text=True,
                capture_output=capture,
                timeout=timeout,
                cwd=str(cwd) if cwd is not None else None,
            )
        except subprocess.TimeoutExpired as error:
            raise DeploymentError(
                f"command timed out after {timeout:g}s: {' '.join(arguments)}"
            ) from error
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or error.stdout or "command failed").strip()
            raise DeploymentError(f"{' '.join(arguments)}: {detail}") from error
        except OSError as error:
            raise DeploymentError(f"cannot run {arguments[0]}: {error}") from error

    def stop(self, timeout: float = 30.0) -> None:
        load_state = self.run(
            [
                "systemctl",
                "show",
                self.service_name,
                "--property=LoadState",
                "--value",
            ],
            timeout=10.0,
        ).stdout.strip()
        if load_state in ("", "not-found"):
            return
        self.run(["systemctl", "stop", self.service_name], timeout=timeout)

    def daemon_reload(self) -> None:
        self.run(["systemctl", "daemon-reload"], timeout=30.0)

    def start_and_verify(
        self,
        timeout: float,
        settle_seconds: float,
    ) -> None:
        # Type=notify makes this return only after the runtime sends READY=1.
        self.run(["systemctl", "start", self.service_name], timeout=timeout)
        deadline = time.monotonic() + max(0.0, settle_seconds)
        restart_count: Optional[str] = None
        while True:
            active = self.run(
                ["systemctl", "is-active", self.service_name],
                timeout=10.0,
            ).stdout.strip()
            if active != "active":
                raise DeploymentError(
                    f"{self.service_name} is {active or 'not active'} after startup"
                )
            details = self.run(
                [
                    "systemctl",
                    "show",
                    self.service_name,
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=Result",
                    "--property=ExecMainStatus",
                    "--property=MainPID",
                    "--property=NRestarts",
                ],
                timeout=10.0,
            ).stdout
            values = dict(
                line.split("=", 1)
                for line in details.splitlines()
                if "=" in line
            )
            if values.get("ActiveState") != "active" or values.get("SubState") != "running":
                raise DeploymentError(
                    f"{self.service_name} did not remain active/running: {values}"
                )
            if values.get("Result", "success") != "success":
                raise DeploymentError(
                    f"{self.service_name} startup result is {values.get('Result')}"
                )
            if values.get("ExecMainStatus", "0") != "0":
                raise DeploymentError(
                    f"{self.service_name} main process status is "
                    f"{values.get('ExecMainStatus')}"
                )
            try:
                main_pid = int(values.get("MainPID", "0"))
            except ValueError as error:
                raise DeploymentError(
                    f"{self.service_name} returned an invalid MainPID"
                ) from error
            if main_pid <= 0:
                raise DeploymentError(f"{self.service_name} has no running main process")
            observed_restarts = values.get("NRestarts", "0")
            if restart_count is None:
                restart_count = observed_restarts
            elif observed_restarts != restart_count:
                raise DeploymentError(
                    f"{self.service_name} restarted during the stability window"
                )
            if time.monotonic() >= deadline:
                return
            time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


class ReleaseDeployer:
    """Prepare fully, switch once, and restore the prior release on failure."""

    def __init__(
        self,
        paths: DeploymentPaths,
        *,
        systemd: Optional[SystemdManager] = None,
        health_timeout: float = 150.0,
        settle_seconds: float = 5.0,
        minimum_free_bytes: int = 512 * 1024 * 1024,
        python_executable: str = sys.executable,
        now: Callable[[], str] = utc_now,
    ) -> None:
        self.paths = DeploymentPaths(
            source=paths.source.resolve(),
            prefix=paths.prefix.resolve(),
            state_directory=paths.state_directory.resolve(),
            service_unit=Path(os.path.abspath(paths.service_unit)),
        )
        self.systemd = systemd or SystemdManager()
        self.health_timeout = max(30.0, float(health_timeout))
        self.settle_seconds = max(0.0, min(60.0, float(settle_seconds)))
        self.minimum_free_bytes = max(0, int(minimum_free_bytes))
        self.python_executable = python_executable
        self.now = now

    def _source_paths(self) -> list[Path]:
        missing = [name for name in RELEASE_FILES if not (self.paths.source / name).exists()]
        if missing:
            raise DeploymentError(
                "release source is missing: " + ", ".join(sorted(missing))
            )
        files: list[Path] = []
        for name in RELEASE_FILES:
            path = self.paths.source / name
            if path.is_symlink():
                raise DeploymentError(f"release source cannot be a symlink: {path}")
            if path.is_dir():
                files.extend(_tree_files(path))
            elif path.is_file():
                files.append(path)
            else:
                raise DeploymentError(f"release source is not a regular entry: {path}")
        return files

    def _required_space(self, source_files: list[Path]) -> int:
        source_bytes = sum(path.stat().st_size for path in source_files)
        mutable_bytes = 0
        for name in STATE_FILES:
            path = self.paths.state_directory / name
            if path.is_file():
                mutable_bytes += path.stat().st_size
        for name in SHARED_CONTENT:
            path = self.paths.prefix / name
            if path.is_file():
                mutable_bytes += path.stat().st_size
            elif path.is_dir():
                mutable_bytes += sum(item.stat().st_size for item in _tree_files(path))
        active_venv = self.paths.current / "venv"
        if not active_venv.is_dir():
            active_venv = self.paths.prefix / "venv"
        venv_bytes = 0
        if active_venv.is_dir():
            try:
                venv_bytes = sum(
                    path.stat().st_size
                    for path in active_venv.rglob("*")
                    if path.is_file() and not path.is_symlink()
                )
            except OSError:
                venv_bytes = 0
        return max(
            self.minimum_free_bytes,
            source_bytes * 4 + mutable_bytes * 2,
            int(venv_bytes * 1.25) + mutable_bytes * 2,
        )

    def preflight(self) -> None:
        source_files = self._source_paths()
        for command in ("systemctl",):
            if not self.systemd.command_available(command):
                raise DeploymentError(f"required command is unavailable: {command}")
        for path in (
            self.paths.releases,
            self.paths.deployment_state,
            self.paths.backups,
        ):
            if path.is_symlink():
                raise DeploymentError(f"managed deployment path cannot be a symlink: {path}")
        for name in STATE_FILES:
            path = self.paths.state_directory / name
            if path.is_symlink():
                raise DeploymentError(f"mutable state cannot be a symlink: {path}")
            if path.exists() and not path.is_file():
                raise DeploymentError(f"mutable state must be a regular file: {path}")
            if path.is_file() and path.stat().st_size > MAX_STATE_FILE_BYTES:
                raise DeploymentError(
                    f"mutable state cannot exceed {MAX_STATE_FILE_BYTES // 1024} KiB: {path}"
                )
        for name in SHARED_CONTENT:
            path = self.paths.prefix / name
            if path.is_symlink():
                raise DeploymentError(f"shared content cannot be a symlink: {path}")
            expected_directory = name == "sounds"
            if path.exists() and expected_directory != path.is_dir():
                kind = "directory" if expected_directory else "regular file"
                raise DeploymentError(f"shared content must be a {kind}: {path}")
            if path.is_file():
                _bounded_content((path,))
            elif path.is_dir():
                _bounded_content(_tree_files(path))
        if self.paths.service_unit.is_symlink():
            raise DeploymentError(
                f"systemd unit cannot be a symlink: {self.paths.service_unit}"
            )
        if self.paths.service_unit.exists() and not self.paths.service_unit.is_file():
            raise DeploymentError(
                f"systemd unit must be a regular file: {self.paths.service_unit}"
            )
        try:
            self.paths.prefix.mkdir(parents=True, exist_ok=True)
            self.paths.releases.mkdir(parents=True, exist_ok=True)
            self.paths.state_directory.mkdir(parents=True, exist_ok=True)
            self.paths.deployment_state.mkdir(parents=True, exist_ok=True)
            self.paths.backups.mkdir(parents=True, exist_ok=True)
            os.chmod(self.paths.state_directory, 0o750)
            os.chmod(self.paths.deployment_state, 0o700)
            os.chmod(self.paths.backups, 0o700)
        except OSError as error:
            raise DeploymentError(f"cannot create deployment directories: {error}") from error
        available = shutil.disk_usage(self.paths.prefix).free
        required = self._required_space(source_files)
        if available < required:
            raise DeploymentError(
                f"insufficient free space: need {required // (1024 * 1024)} MiB, "
                f"have {available // (1024 * 1024)} MiB"
            )
        self._current_release()

    def _current_release(self) -> Optional[Path]:
        current = self.paths.current
        if not current.exists() and not current.is_symlink():
            return None
        if not current.is_symlink():
            raise DeploymentError(f"current release path must be a symlink: {current}")
        try:
            target = current.resolve(strict=True)
            target.relative_to(self.paths.releases)
        except (OSError, ValueError) as error:
            raise DeploymentError(
                f"current release must point inside {self.paths.releases}: {error}"
            ) from error
        if not target.is_dir():
            raise DeploymentError(f"current release is not a directory: {target}")
        return target

    def _copy_release(self, destination: Path) -> None:
        destination.mkdir(mode=0o755, parents=True, exist_ok=False)
        try:
            for name in RELEASE_FILES:
                source = self.paths.source / name
                target = destination / name
                if source.is_dir():
                    shutil.copytree(
                        source,
                        target,
                        symlinks=False,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
                    )
                else:
                    shutil.copy2(source, target)
            sounds = self.paths.source / "sounds"
            if sounds.exists():
                if sounds.is_symlink() or not sounds.is_dir():
                    raise DeploymentError("source sounds entry must be a real directory")
                _bounded_content(_tree_files(sounds))
                shutil.copytree(sounds, destination / "sounds", symlinks=False)
            else:
                (destination / "sounds").mkdir(mode=0o755)
        except (OSError, shutil.Error) as error:
            raise DeploymentError(f"cannot stage release files: {error}") from error

    def _effective_content(self, release: Path) -> tuple[Path, Path, Path]:
        personalities = self.paths.prefix / "personalities.json"
        scenes = self.paths.prefix / "scenes.json"
        sounds = self.paths.prefix / "sounds"
        return (
            personalities if personalities.is_file() else release / "personalities.json",
            scenes if scenes.is_file() else release / "scenes.json",
            sounds if sounds.is_dir() else release / "sounds",
        )

    def _prepare_runtime(self, release: Path) -> None:
        venv = release / "venv"
        self.systemd.run(
            [self.python_executable, "-m", "venv", str(venv)],
            timeout=180.0,
            capture=False,
        )
        python = venv / "bin" / "python"
        self.systemd.run(
            [
                str(python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--isolated",
                "--no-cache-dir",
                "--requirement",
                str(release / "requirements.txt"),
            ],
            timeout=900.0,
            capture=False,
        )
        self.systemd.run(
            [
                str(python),
                "-m",
                "compileall",
                "-q",
                str(release / "holiday_skeleton"),
                str(release / "skeleton_all_in_one_mqtt.py"),
            ],
            timeout=120.0,
        )
        self.systemd.run(
            [str(python), "-c", "import skeleton_all_in_one_mqtt"],
            timeout=60.0,
            capture=True,
            cwd=release,
        )
        personalities, scenes, sounds = self._effective_content(release)
        validation = (
            "from holiday_skeleton.content import prepare_content; "
            "prepare_content(personalities_enabled=True, "
            f"personalities_path={str(personalities)!r}, requested_personality='', "
            "current_personality='', scenes_enabled=True, "
            f"scenes_path={str(scenes)!r}, sound_directory={str(sounds)!r}, "
            "sound_sample_rate=22050, scene_maximum_seconds=30.0, cache_sounds=False)"
        )
        self.systemd.run(
            [str(python), "-c", validation],
            timeout=120.0,
            capture=True,
            cwd=release,
        )
        if self.systemd.command_available("systemd-analyze"):
            rendered = self._render_unit(release).decode("utf-8").replace(
                str(self.paths.current), str(release)
            )
            temporary: Optional[Path] = None
            try:
                descriptor, name = tempfile.mkstemp(suffix=".service")
                temporary = Path(name)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(rendered)
                self.systemd.run(
                    ["systemd-analyze", "verify", str(temporary)],
                    timeout=30.0,
                    capture=True,
                )
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def _manifest_inventory(self, release: Path) -> dict[str, dict[str, Any]]:
        files: dict[str, dict[str, Any]] = {}
        for path in sorted(release.rglob("*")):
            relative = path.relative_to(release).as_posix()
            if relative == "release-manifest.json":
                continue
            if path.is_symlink():
                files[relative] = {
                    "type": "symlink",
                    "target": os.readlink(path),
                }
            elif path.is_file():
                files[relative] = {
                    "type": "file",
                    "bytes": path.stat().st_size,
                    "sha256": sha256_file(path),
                }
            elif path.is_dir():
                continue
            else:
                raise DeploymentError(f"release has an unsupported entry: {path}")
        return files

    def _release_manifest(self, release_id: str, release: Path) -> dict[str, Any]:
        files = self._manifest_inventory(release)
        source_commit = "unknown"
        try:
            completed = subprocess.run(
                ["git", "-C", str(self.paths.source), "rev-parse", "HEAD"],
                check=True,
                text=True,
                capture_output=True,
                timeout=10.0,
            )
            candidate = completed.stdout.strip().lower()
            if re.fullmatch(r"[a-f0-9]{40}", candidate):
                source_commit = candidate
        except (OSError, subprocess.SubprocessError):
            pass
        return {
            "version": 1,
            "release_id": release_id,
            "created_at": self.now(),
            "source_commit": source_commit,
            "files": files,
        }

    def _verify_manifest(self, release: Path) -> None:
        manifest_path = release / "release-manifest.json"
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DeploymentError(f"cannot read release manifest: {error}") from error
        expected = manifest.get("files")
        if not isinstance(expected, dict):
            raise DeploymentError("release manifest files must be an object")
        actual = self._manifest_inventory(release)
        if set(expected) != set(actual):
            raise DeploymentError("release manifest file inventory changed after staging")
        for name, record in expected.items():
            if not isinstance(record, dict) or record != actual[name]:
                raise DeploymentError(f"release file changed after staging: {name}")

    def _prepare_release(self, release_id: str) -> Path:
        destination = self.paths.releases / release_id
        if destination.exists() or destination.is_symlink():
            raise DeploymentError(f"release already exists: {destination}")
        staging = self.paths.releases / f".staging-{release_id}-{uuid.uuid4().hex[:8]}"
        try:
            self._copy_release(staging)
            self._prepare_runtime(staging)
            manifest = self._release_manifest(release_id, staging)
            _atomic_json(staging / "release-manifest.json", manifest, mode=0o644)
            self._verify_manifest(staging)
            os.replace(staging, destination)
            return destination
        except BaseException:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            raise

    def _copy_backup_entry(self, source: Path, destination: Path) -> None:
        if source.is_symlink():
            raise DeploymentError(f"refusing to back up symlinked mutable data: {source}")
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            os.chmod(destination, 0o600)
        elif source.is_dir():
            files = _tree_files(source)
            _bounded_content(files)
            shutil.copytree(source, destination, symlinks=False)
            for path in destination.rglob("*"):
                os.chmod(path, 0o700 if path.is_dir() else 0o600)

    def _create_backup(
        self,
        release_id: str,
        previous: Optional[Path],
        release: Path,
    ) -> Path:
        stamp = re.sub(r"[^0-9]", "", self.now())[:14] or "unknown"
        backup = self.paths.backups / f"{stamp}-{release_id}-{uuid.uuid4().hex[:6]}"
        backup.mkdir(mode=0o700, parents=True, exist_ok=False)
        try:
            present: dict[str, bool] = {}
            for name in STATE_FILES:
                source = self.paths.state_directory / name
                present[f"state/{name}"] = source.exists()
                if source.exists():
                    self._copy_backup_entry(source, backup / "state" / name)
            for name in SHARED_CONTENT:
                source = self.paths.prefix / name
                present[f"content/{name}"] = source.exists()
                if source.exists():
                    self._copy_backup_entry(source, backup / "content" / name)
            unit_exists = self.paths.service_unit.exists()
            if unit_exists:
                self._copy_backup_entry(
                    self.paths.service_unit,
                    backup / "systemd" / self.paths.service_unit.name,
                )
            metadata = {
                "version": DEPLOYMENT_RECORD_VERSION,
                "created_at": self.now(),
                "release_id": release_id,
                "deployed_release": str(release),
                "previous_release": str(previous) if previous is not None else None,
                "unit_existed": unit_exists,
                "present": present,
            }
            _atomic_json(backup / "backup.json", metadata)
            return backup
        except BaseException:
            shutil.rmtree(backup, ignore_errors=True)
            raise

    def _seed_shared_content(self, release: Path) -> None:
        for name in SHARED_CONTENT:
            target = self.paths.prefix / name
            if target.exists():
                continue
            source = release / name
            if name == "sounds":
                shutil.copytree(source, target, symlinks=False)
            else:
                shutil.copy2(source, target)

    def _render_unit(self, release: Path) -> bytes:
        source = release / "systemd" / "holiday-skeleton.service"
        try:
            text = source.read_text(encoding="utf-8")
        except OSError as error:
            raise DeploymentError(f"cannot read packaged systemd unit: {error}") from error
        text = text.replace("/opt/holiday-skeleton", str(self.paths.prefix))
        if "\x00" in text or "\r" in text:
            raise DeploymentError("packaged systemd unit contains unsafe characters")
        return text.encode("utf-8")

    def _switch_current(self, release: Optional[Path]) -> None:
        current = self.paths.current
        temporary = self.paths.prefix / f".current-{uuid.uuid4().hex[:8]}"
        if release is None:
            if current.is_symlink():
                current.unlink()
            elif current.exists():
                raise DeploymentError(f"cannot remove non-symlink current path: {current}")
            return
        try:
            relative = release.relative_to(self.paths.prefix)
        except ValueError as error:
            raise DeploymentError("release target is outside the deployment prefix") from error
        try:
            os.symlink(relative, temporary)
            os.replace(temporary, current)
        except OSError as error:
            raise DeploymentError(f"cannot atomically switch current release: {error}") from error
        finally:
            if temporary.is_symlink():
                temporary.unlink()

    def _restore_entry(self, backup: Path, target: Path, was_present: bool) -> None:
        if not was_present:
            if target.is_symlink() or target.is_file():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            return
        if not backup.exists():
            raise DeploymentError(f"backup is missing required entry: {backup}")
        if backup.is_file():
            _atomic_write(target, backup.read_bytes(), mode=0o600)
            return
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        shutil.copytree(backup, target, symlinks=False)

    def _load_backup(self, backup: Path) -> dict[str, Any]:
        metadata_path = backup / "backup.json"
        try:
            payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DeploymentError(f"cannot read deployment backup: {error}") from error
        expected = {
            "version",
            "created_at",
            "release_id",
            "deployed_release",
            "previous_release",
            "unit_existed",
            "present",
        }
        if not isinstance(payload, dict) or set(payload) != expected:
            raise DeploymentError("deployment backup has an invalid schema")
        if payload.get("version") != DEPLOYMENT_RECORD_VERSION:
            raise DeploymentError("deployment backup version is unsupported")
        if not isinstance(payload.get("present"), dict):
            raise DeploymentError("deployment backup presence map is invalid")
        return payload

    def _restore_backup(self, backup: Path) -> dict[str, Any]:
        metadata = self._load_backup(backup)
        present = metadata["present"]
        for name in STATE_FILES:
            key = f"state/{name}"
            self._restore_entry(
                backup / "state" / name,
                self.paths.state_directory / name,
                bool(present.get(key, False)),
            )
        for name in SHARED_CONTENT:
            key = f"content/{name}"
            self._restore_entry(
                backup / "content" / name,
                self.paths.prefix / name,
                bool(present.get(key, False)),
            )
        if metadata["unit_existed"]:
            unit_backup = backup / "systemd" / self.paths.service_unit.name
            _atomic_write(self.paths.service_unit, unit_backup.read_bytes(), mode=0o644)
        elif self.paths.service_unit.exists():
            self.paths.service_unit.unlink()
        return metadata

    def _rollback_transaction(
        self,
        previous: Optional[Path],
        backup: Path,
    ) -> None:
        try:
            self.systemd.stop()
        except DeploymentError as error:
            raise DeploymentError(
                f"cannot stop the candidate safely; rollback was not attempted: {error}"
            ) from error
        try:
            self._switch_current(previous)
            metadata = self._restore_backup(backup)
            self.systemd.daemon_reload()
        except (DeploymentError, OSError, shutil.Error) as error:
            raise DeploymentError(
                f"candidate is stopped but the prior release could not be restored: {error}"
            ) from error
        if previous is not None or (metadata is not None and metadata["unit_existed"]):
            try:
                self.systemd.start_and_verify(self.health_timeout, self.settle_seconds)
            except DeploymentError as error:
                raise DeploymentError(
                    f"prior release was restored but its health check failed: {error}"
                ) from error

    def deploy(
        self, release_id: str, *, simulate_activation_failure: bool = False
    ) -> DeploymentResult:
        release_id = validate_release_id(release_id)
        self.preflight()
        previous = self._current_release()
        prior_unit_exists = self.paths.service_unit.exists()
        release = self._prepare_release(release_id)
        backup: Optional[Path] = None
        try:
            self.systemd.stop()
            backup = self._create_backup(release_id, previous, release)
            self._seed_shared_content(release)
            _atomic_write(self.paths.service_unit, self._render_unit(release), mode=0o644)
            self._switch_current(release)
            self.systemd.daemon_reload()
            if simulate_activation_failure:
                raise DeploymentError(
                    "operator-requested activation failure for rollback acceptance"
                )
            self.systemd.start_and_verify(self.health_timeout, self.settle_seconds)
            record = {
                "version": DEPLOYMENT_RECORD_VERSION,
                "status": "active",
                "completed_at": self.now(),
                "release_id": release_id,
                "release_path": str(release),
                "previous_release": str(previous) if previous is not None else None,
                "backup_path": str(backup),
            }
            _atomic_json(self.paths.record, record)
        except BaseException as error:
            if backup is not None:
                try:
                    self._rollback_transaction(previous, backup)
                except DeploymentError as rollback_error:
                    raise DeploymentError(
                        f"deployment failed: {error}; automatic rollback also failed: "
                        f"{rollback_error}"
                    ) from error
                raise DeploymentError(
                    f"deployment failed and was rolled back successfully: {error}"
                ) from error
            if previous is not None or prior_unit_exists:
                try:
                    self.systemd.start_and_verify(
                        self.health_timeout, self.settle_seconds
                    )
                except DeploymentError as recovery_error:
                    raise DeploymentError(
                        f"deployment stopped before activation: {error}; "
                        f"the prior service also failed to restart: {recovery_error}"
                    ) from error
            raise DeploymentError(f"deployment stopped before activation: {error}") from error
        assert backup is not None
        return DeploymentResult(release_id, release, previous, backup)

    def rollback_last(self) -> DeploymentResult:
        try:
            record = json.loads(self.paths.record.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise DeploymentError(f"cannot read last deployment record: {error}") from error
        required = {
            "version",
            "status",
            "completed_at",
            "release_id",
            "release_path",
            "previous_release",
            "backup_path",
        }
        valid_fields = (required, required | {"rolled_back_at"})
        if not isinstance(record, dict) or set(record) not in valid_fields:
            raise DeploymentError("last deployment record has an invalid schema")
        if record["version"] != DEPLOYMENT_RECORD_VERSION or record["status"] != "active":
            raise DeploymentError("last deployment is not an active rollback candidate")
        release = Path(record["release_path"]).resolve()
        previous_value = record["previous_release"]
        previous = Path(previous_value).resolve() if previous_value else None
        backup = Path(record["backup_path"]).resolve()
        for path, label, root in (
            (release, "release", self.paths.releases),
            (backup, "backup", self.paths.backups),
        ):
            try:
                path.relative_to(root)
            except ValueError as error:
                raise DeploymentError(
                    f"recorded {label} path is outside its managed root"
                ) from error
        if previous is not None:
            try:
                previous.relative_to(self.paths.releases)
            except ValueError as error:
                raise DeploymentError("recorded previous release is outside releases") from error
        current = self._current_release()
        if current != release:
            raise DeploymentError(
                "current release no longer matches the recorded deployment; refusing stale rollback"
            )
        recovery_backup: Optional[Path] = None
        try:
            self.systemd.stop()
            recovery_backup = self._create_backup(
                "rollback-recovery",
                current,
                current,
            )
            self._switch_current(previous)
            metadata = self._restore_backup(backup)
            self.systemd.daemon_reload()
            if previous is not None or metadata["unit_existed"]:
                self.systemd.start_and_verify(
                    self.health_timeout, self.settle_seconds
                )
        except BaseException as error:
            if recovery_backup is not None:
                try:
                    self._rollback_transaction(current, recovery_backup)
                except DeploymentError as recovery_error:
                    raise DeploymentError(
                        f"manual rollback failed: {error}; restoring the active "
                        f"release also failed: {recovery_error}"
                    ) from error
                raise DeploymentError(
                    f"manual rollback failed; the active release was restored: {error}"
                ) from error
            try:
                self.systemd.start_and_verify(
                    self.health_timeout, self.settle_seconds
                )
            except DeploymentError as recovery_error:
                raise DeploymentError(
                    f"manual rollback stopped before its recovery snapshot: {error}; "
                    f"the active release also failed to restart: {recovery_error}"
                ) from error
            raise DeploymentError(
                f"manual rollback stopped before its recovery snapshot: {error}"
            ) from error
        record["status"] = "rolled_back"
        record["rolled_back_at"] = self.now()
        # The completed record intentionally gets a new exact schema.
        _atomic_json(self.paths.record, record)
        return DeploymentResult(
            str(record["release_id"]),
            release,
            previous,
            backup,
            rolled_back=True,
        )
