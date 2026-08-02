"""Auditable Raspberry Pi release-candidate acceptance evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from .event_journal import sanitize_detail


EVIDENCE_VERSION = 1
MAX_EVIDENCE_BYTES = 512 * 1024
MAX_RELEASE_MANIFEST_BYTES = 32 * 1024 * 1024
MIN_SOAK_SECONDS = 8 * 60 * 60
MAX_SOAK_SECONDS = 72 * 60 * 60
MIN_SOAK_SAMPLES = 3
MAX_SOAK_SAMPLES = 256
MAX_OPERATOR_CHARS = 80
_CANDIDATE_PATTERN = re.compile(r"^v1\.0-rc[1-9][0-9]{0,2}$")
_COMMIT_PATTERN = re.compile(r"^[a-f0-9]{40}$")


@dataclass(frozen=True)
class AcceptanceCheck:
    identifier: str
    title: str
    prerequisite_for_soak: bool = True


ACCEPTANCE_CHECKS = (
    AcceptanceCheck("deploy_verified", "Versioned deployment and manifest"),
    AcceptanceCheck("calibration_self_test", "Calibration and physical output self-test"),
    AcceptanceCheck("conversation_audio", "Conversation, cache, memory, and audio timing"),
    AcceptanceCheck("barge_in_preemption", "Barge-in and PIR/MQTT preemption"),
    AcceptanceCheck("scenes_content", "Scenes and transactional content rollback"),
    AcceptanceCheck("maintenance_lockout", "Maintenance lockout and safe outputs"),
    AcceptanceCheck("settings_restart", "Saved settings and clean service restart"),
    AcceptanceCheck("power_cycle", "Full Raspberry Pi power-cycle recovery"),
    AcceptanceCheck("watchdog_recovery", "Controlled systemd watchdog recovery"),
    AcceptanceCheck("deployment_rollback", "Injected activation failure and automatic rollback"),
    AcceptanceCheck("manual_rollback", "Manual rollback and recovery"),
    AcceptanceCheck("journal_privacy", "Diagnostic journal retention and privacy"),
    AcceptanceCheck("final_inspection", "Post-soak mechanical and electrical inspection", False),
)
CHECK_BY_ID = {check.identifier: check for check in ACCEPTANCE_CHECKS}


class ReleaseCandidateError(ValueError):
    """Release-candidate evidence is missing, unsafe, or incomplete."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not 10 <= len(value) <= 64:
        raise ReleaseCandidateError(f"{label} must be a short ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ReleaseCandidateError(f"{label} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ReleaseCandidateError(f"{label} must include a timezone")
    return parsed


def _bounded_text(value: object, label: str, maximum: int) -> str:
    text = " ".join(str(value or "").replace("\x00", " ").splitlines()).strip()
    if not text or len(text) > maximum:
        raise ReleaseCandidateError(f"{label} must be 1-{maximum} characters")
    return text


def _operator_note(value: object) -> str:
    note = _bounded_text(value, "evidence note", 240)
    sanitized = sanitize_detail(note)
    if "[redacted]" in sanitized:
        raise ReleaseCandidateError("evidence notes must not contain credentials")
    return sanitized


def _candidate_name(value: object) -> str:
    candidate = str(value or "").strip().lower()
    if not _CANDIDATE_PATTERN.fullmatch(candidate):
        raise ReleaseCandidateError("candidate must use the form v1.0-rc1")
    return candidate


def _commit(value: object) -> str:
    commit = str(value or "").strip().lower()
    if not _COMMIT_PATTERN.fullmatch(commit):
        raise ReleaseCandidateError("expected commit must be a full 40-character Git SHA")
    return commit


def _exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise ReleaseCandidateError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise ReleaseCandidateError(f"{label} is missing fields: {', '.join(missing)}")


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ReleaseCandidateError(f"evidence path cannot be a symlink: {path}")
    temporary: Optional[Path] = None
    try:
        descriptor, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, allow_nan=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except (OSError, TypeError, ValueError) as error:
        raise ReleaseCandidateError(f"cannot write acceptance evidence: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def evidence_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_digest(path: Path) -> Path:
    payload = load_evidence(path)
    if payload.get("status") != "passed" or payload.get("signoff") is None:
        raise ReleaseCandidateError("only finalized evidence can receive a digest")
    digest_path = path.with_suffix(path.suffix + ".sha256")
    if digest_path.is_symlink():
        raise ReleaseCandidateError(f"evidence digest path cannot be a symlink: {digest_path}")
    digest = evidence_digest(path)
    temporary: Optional[Path] = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{digest_path.name}.", dir=str(digest_path.parent)
        )
        temporary = Path(name)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(f"{digest}  {path.name}\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, digest_path)
        os.chmod(digest_path, 0o600)
    except OSError as error:
        raise ReleaseCandidateError(f"cannot write evidence digest: {error}") from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return digest_path


def _read_json(path: Path, label: str, maximum_bytes: int) -> Mapping[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReleaseCandidateError(f"{label} must be a regular file: {path}")
    if path.stat().st_size > maximum_bytes:
        raise ReleaseCandidateError(f"{label} exceeds {maximum_bytes // 1024} KiB")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReleaseCandidateError(f"cannot read {label}: {error}") from error
    if not isinstance(payload, dict):
        raise ReleaseCandidateError(f"{label} must be a JSON object")
    return payload


def _parse_properties(output: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for line in output.splitlines():
        key, separator, value = line.partition("=")
        if separator and key:
            properties[key] = value
    return properties


def _watchdog_enabled(value: object) -> bool:
    rendered = str(value or "").strip().lower()
    if not rendered or rendered in ("0", "0us", "infinity"):
        return False
    if rendered.isdigit():
        return int(rendered) > 0
    return bool(re.fullmatch(r"[1-9][0-9]*(?:\.[0-9]+)?(?:us|ms|s|min|h)", rendered))


def _has_mid_soak_sample(
    timestamps: Sequence[datetime], started: datetime, required_seconds: int
) -> bool:
    earliest = started + timedelta(seconds=required_seconds * 0.25)
    latest = started + timedelta(seconds=required_seconds * 0.75)
    return any(earliest <= timestamp <= latest for timestamp in timestamps)


def _memory_used_percent(meminfo: str) -> float:
    values: dict[str, int] = {}
    for line in meminfo.splitlines():
        key, separator, remainder = line.partition(":")
        if separator:
            try:
                values[key] = int(remainder.strip().split()[0])
            except (IndexError, ValueError):
                continue
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", 0)
    if total <= 0 or not 0 <= available <= total:
        raise ReleaseCandidateError("cannot parse Raspberry Pi memory information")
    return round((total - available) * 100.0 / total, 2)


def _journal_summary(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {
            "mode": None,
            "events": 0,
            "warnings": 0,
            "errors": 0,
            "latest_code": None,
            "active_session": False,
        }
    payload = _read_json(path, "diagnostic journal", 256 * 1024)
    events = payload.get("events")
    if not isinstance(events, list):
        raise ReleaseCandidateError("diagnostic journal events must be a list")
    warning_count = 0
    error_count = 0
    latest_code = None
    for event in events:
        if not isinstance(event, dict):
            raise ReleaseCandidateError("diagnostic journal contains an invalid event")
        severity = event.get("severity")
        if severity == "warning":
            warning_count += 1
        elif severity == "error":
            error_count += 1
        code = event.get("code")
        latest_code = code if isinstance(code, str) else None
    return {
        "mode": f"{path.stat().st_mode & 0o777:04o}",
        "events": len(events),
        "warnings": warning_count,
        "errors": error_count,
        "latest_code": latest_code,
        "active_session": bool(payload.get("active_session")),
    }


def _verify_runtime_manifest(release: Path, manifest_path: Path, manifest: Mapping[str, Any]) -> tuple[str, int]:
    inventory = manifest.get("files")
    if not isinstance(inventory, dict) or not inventory:
        raise ReleaseCandidateError("release manifest has no file inventory")
    if len(inventory) > 100_000:
        raise ReleaseCandidateError("release manifest has too many file entries")
    verified = 0
    required = {"skeleton_all_in_one_mqtt.py", "requirements.txt"}
    for name, record in inventory.items():
        if not isinstance(name, str) or not name or name.startswith("/"):
            raise ReleaseCandidateError("release manifest contains an unsafe path")
        parts = Path(name).parts
        if ".." in parts or "." in parts:
            raise ReleaseCandidateError("release manifest contains an unsafe path")
        if name.startswith("venv/"):
            continue
        if not isinstance(record, dict):
            raise ReleaseCandidateError(f"release manifest record is invalid: {name}")
        path = release.joinpath(*parts)
        try:
            path.relative_to(release)
        except ValueError as error:
            raise ReleaseCandidateError("release manifest path escaped the release") from error
        entry_type = record.get("type")
        if entry_type == "file":
            if path.is_symlink() or not path.is_file():
                raise ReleaseCandidateError(f"release runtime file is missing: {name}")
            expected_hash = record.get("sha256")
            expected_bytes = record.get("bytes")
            if (
                not isinstance(expected_hash, str)
                or not re.fullmatch(r"[a-f0-9]{64}", expected_hash)
                or isinstance(expected_bytes, bool)
                or not isinstance(expected_bytes, int)
                or expected_bytes < 0
            ):
                raise ReleaseCandidateError(f"release manifest file record is invalid: {name}")
            if path.stat().st_size != expected_bytes or _sha256_file(path) != expected_hash:
                raise ReleaseCandidateError(f"release runtime file changed after deployment: {name}")
        elif entry_type == "symlink":
            target = record.get("target")
            if not isinstance(target, str) or not path.is_symlink() or os.readlink(path) != target:
                raise ReleaseCandidateError(f"release runtime symlink changed after deployment: {name}")
        else:
            raise ReleaseCandidateError(f"release manifest entry type is invalid: {name}")
        verified += 1
        required.discard(name)
    if required:
        raise ReleaseCandidateError(
            "release manifest is missing runtime files: " + ", ".join(sorted(required))
        )
    return _sha256_file(manifest_path), verified


class SystemProbe:
    """Collect a small, credential-free acceptance sample from the installed Pi."""

    def __init__(
        self,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        now: Callable[[], str] = utc_now,
        temperature_path: Path = Path("/sys/class/thermal/thermal_zone0/temp"),
        meminfo_path: Path = Path("/proc/meminfo"),
        uptime_path: Path = Path("/proc/uptime"),
    ) -> None:
        self.runner = runner
        self.now = now
        self.temperature_path = temperature_path
        self.meminfo_path = meminfo_path
        self.uptime_path = uptime_path

    def _run(self, command: Sequence[str]) -> str:
        try:
            completed = self.runner(
                list(command), check=True, capture_output=True, text=True, timeout=15.0
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise ReleaseCandidateError(
                f"acceptance probe failed: {' '.join(command[:2])}: {error}"
            ) from error
        return completed.stdout.strip()

    def capture(
        self,
        *,
        prefix: Path = Path("/opt/holiday-skeleton"),
        state_directory: Path = Path("/var/lib/holiday-skeleton"),
        service_name: str = "holiday-skeleton",
    ) -> dict[str, Any]:
        properties = _parse_properties(
            self._run(
                (
                    "systemctl",
                    "show",
                    service_name,
                    "--property=ActiveState",
                    "--property=SubState",
                    "--property=Result",
                    "--property=ExecMainStatus",
                    "--property=MainPID",
                    "--property=NRestarts",
                    "--property=WatchdogUSec",
                )
            )
        )
        required_properties = {
            "ActiveState",
            "SubState",
            "Result",
            "ExecMainStatus",
            "MainPID",
            "NRestarts",
            "WatchdogUSec",
        }
        if not required_properties.issubset(properties):
            raise ReleaseCandidateError("systemd did not return all required properties")

        current = prefix / "current"
        if not current.is_symlink():
            raise ReleaseCandidateError("current release must be an atomic symlink")
        try:
            release = current.resolve(strict=True)
            release.relative_to((prefix / "releases").resolve())
        except (OSError, ValueError) as error:
            raise ReleaseCandidateError("current release is outside managed releases") from error
        manifest_path = release / "release-manifest.json"
        manifest = _read_json(
            manifest_path, "release manifest", MAX_RELEASE_MANIFEST_BYTES
        )
        release_id = str(manifest.get("release_id") or "")
        source_commit = str(manifest.get("source_commit") or "").lower()
        if release.name != release_id or not _COMMIT_PATTERN.fullmatch(source_commit):
            raise ReleaseCandidateError("active release manifest identity is invalid")
        try:
            manifest_hash, verified_runtime_files = _verify_runtime_manifest(
                release, manifest_path, manifest
            )
        except OSError as error:
            raise ReleaseCandidateError(
                f"cannot verify active release files: {error}"
            ) from error

        try:
            temperature_c = round(float(self.temperature_path.read_text().strip()) / 1000.0, 2)
            memory_percent = _memory_used_percent(self.meminfo_path.read_text())
            uptime_seconds = round(float(self.uptime_path.read_text().split()[0]), 1)
        except (OSError, ValueError, IndexError) as error:
            raise ReleaseCandidateError(f"cannot read Raspberry Pi telemetry: {error}") from error
        throttle_output = self._run(("vcgencmd", "get_throttled"))
        match = re.fullmatch(r"throttled=0x([a-fA-F0-9]+)", throttle_output)
        if not match:
            raise ReleaseCandidateError("vcgencmd returned an invalid throttle value")
        throttle_value = int(match.group(1), 16)
        try:
            disk = shutil.disk_usage(prefix)
        except OSError as error:
            raise ReleaseCandidateError(f"cannot read Pi disk usage: {error}") from error
        disk_used_percent = round((disk.total - disk.free) * 100.0 / disk.total, 2)

        settings = state_directory / "operator-settings.json"
        settings_mode = None
        if settings.exists():
            if settings.is_symlink() or not settings.is_file():
                raise ReleaseCandidateError("operator settings must be a regular file")
            settings_mode = f"{settings.stat().st_mode & 0o777:04o}"

        return {
            "captured_at": self.now(),
            "release": {
                "id": release_id,
                "source_commit": source_commit,
                "path": str(release),
                "manifest_sha256": manifest_hash,
                "runtime_files_verified": verified_runtime_files,
            },
            "service": {key: properties[key] for key in sorted(required_properties)},
            "pi": {
                "temperature_c": temperature_c,
                "throttle_hex": f"0x{throttle_value:x}",
                "current_throttle_flags": throttle_value & 0xF,
                "memory_used_percent": memory_percent,
                "disk_used_percent": disk_used_percent,
                "uptime_seconds": uptime_seconds,
            },
            "state": {
                "settings_mode": settings_mode,
                "journal": _journal_summary(state_directory / "diagnostic-events.json"),
            },
        }


def validate_sample(
    sample: Mapping[str, Any],
    candidate: str,
    expected_commit: str,
    *,
    require_persistent_state: bool = True,
) -> list[str]:
    failures: list[str] = []
    release = sample.get("release", {})
    service = sample.get("service", {})
    pi = sample.get("pi", {})
    state = sample.get("state", {})
    journal = state.get("journal", {}) if isinstance(state, dict) else {}
    if not isinstance(release, dict) or release.get("id") != candidate:
        failures.append("active release does not match the candidate")
    if not isinstance(release, dict) or release.get("source_commit") != expected_commit:
        failures.append("active release commit does not match the expected commit")
    if (
        not isinstance(release, dict)
        or not isinstance(release.get("manifest_sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", release.get("manifest_sha256", ""))
    ):
        failures.append("release manifest was not verified")
    try:
        if int(release.get("runtime_files_verified", 0)) < 2:
            failures.append("release runtime file inventory was not verified")
    except (TypeError, ValueError):
        failures.append("release runtime verification count is invalid")
    if not isinstance(service, dict) or service.get("ActiveState") != "active":
        failures.append("service is not active")
    if not isinstance(service, dict) or service.get("SubState") != "running":
        failures.append("service is not running")
    if not isinstance(service, dict) or service.get("Result") != "success":
        failures.append("service result is not success")
    if not isinstance(service, dict) or service.get("ExecMainStatus") != "0":
        failures.append("service main process status is not zero")
    try:
        if int(service.get("MainPID", "0")) <= 0:
            failures.append("service has no main process")
        if not _watchdog_enabled(service.get("WatchdogUSec")):
            failures.append("systemd watchdog is not enabled")
    except (TypeError, ValueError):
        failures.append("service process/watchdog values are invalid")
    try:
        if int(pi.get("current_throttle_flags", -1)) != 0:
            failures.append("Pi has a current throttle or undervoltage flag")
        if float(pi.get("temperature_c", 999)) >= 82.0:
            failures.append("Pi temperature is at or above the critical threshold")
        if float(pi.get("memory_used_percent", 999)) >= 95.0:
            failures.append("Pi memory use is at or above 95 percent")
        if float(pi.get("disk_used_percent", 999)) >= 97.0:
            failures.append("Pi disk use is at or above 97 percent")
    except (TypeError, ValueError):
        failures.append("Pi telemetry values are invalid")
    if (
        require_persistent_state
        and (not isinstance(state, dict) or state.get("settings_mode") != "0600")
    ):
        failures.append("operator settings are missing or not mode 0600")
    if not isinstance(journal, dict) or journal.get("mode") != "0600":
        failures.append("diagnostic journal is missing or not mode 0600")
    if not isinstance(journal, dict) or not journal.get("active_session"):
        failures.append("diagnostic journal has no active runtime session")
    return failures


def new_evidence(
    candidate: str,
    expected_commit: str,
    *,
    soak_seconds: int = MIN_SOAK_SECONDS,
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    candidate = _candidate_name(candidate)
    expected_commit = _commit(expected_commit)
    seconds = int(soak_seconds)
    if not MIN_SOAK_SECONDS <= seconds <= MAX_SOAK_SECONDS:
        raise ReleaseCandidateError("soak must be between 8 and 72 hours")
    return {
        "version": EVIDENCE_VERSION,
        "candidate": candidate,
        "expected_commit": expected_commit,
        "created_at": now(),
        "status": "in_progress",
        "checks": {
            check.identifier: {"status": "pending", "attempts": []}
            for check in ACCEPTANCE_CHECKS
        },
        "soak": {
            "status": "pending",
            "required_seconds": seconds,
            "started_at": None,
            "completed_at": None,
            "samples": [],
        },
        "signoff": None,
    }


def validate_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    _exact_fields(
        payload,
        {
            "version",
            "candidate",
            "expected_commit",
            "created_at",
            "status",
            "checks",
            "soak",
            "signoff",
        },
        "acceptance evidence",
    )
    if payload.get("version") != EVIDENCE_VERSION:
        raise ReleaseCandidateError("unsupported acceptance evidence version")
    _candidate_name(payload.get("candidate"))
    _commit(payload.get("expected_commit"))
    _parse_timestamp(payload.get("created_at"), "created_at")
    if payload.get("status") not in ("in_progress", "passed"):
        raise ReleaseCandidateError("evidence status must be in_progress or passed")
    checks = payload.get("checks")
    if not isinstance(checks, dict) or set(checks) != set(CHECK_BY_ID):
        raise ReleaseCandidateError("evidence checks do not match the release plan")
    for identifier, record in checks.items():
        if not isinstance(record, dict):
            raise ReleaseCandidateError(f"check {identifier} must be an object")
        _exact_fields(record, {"status", "attempts"}, f"check {identifier}")
        if record.get("status") not in ("pending", "passed", "failed"):
            raise ReleaseCandidateError(f"check {identifier} has an invalid status")
        attempts = record.get("attempts")
        if not isinstance(attempts, list) or len(attempts) > 20:
            raise ReleaseCandidateError(f"check {identifier} attempts are invalid")
        for attempt in attempts:
            if not isinstance(attempt, dict):
                raise ReleaseCandidateError(f"check {identifier} attempt is invalid")
            _exact_fields(attempt, {"recorded_at", "result", "note"}, "check attempt")
            _parse_timestamp(attempt.get("recorded_at"), "check attempt timestamp")
            if attempt.get("result") not in ("passed", "failed"):
                raise ReleaseCandidateError("check attempt result is invalid")
            _operator_note(attempt.get("note"))
        if attempts and record.get("status") != attempts[-1].get("result"):
            raise ReleaseCandidateError(
                f"check {identifier} status does not match its latest attempt"
            )
        if not attempts and record.get("status") != "pending":
            raise ReleaseCandidateError(f"check {identifier} has no supporting attempt")
    soak = payload.get("soak")
    if not isinstance(soak, dict):
        raise ReleaseCandidateError("soak must be an object")
    _exact_fields(
        soak,
        {"status", "required_seconds", "started_at", "completed_at", "samples"},
        "soak",
    )
    if soak.get("status") not in ("pending", "running", "passed"):
        raise ReleaseCandidateError("soak status is invalid")
    required_seconds = soak.get("required_seconds")
    if (
        isinstance(required_seconds, bool)
        or not isinstance(required_seconds, int)
        or not MIN_SOAK_SECONDS <= required_seconds <= MAX_SOAK_SECONDS
    ):
        raise ReleaseCandidateError("soak duration is invalid")
    if soak.get("started_at") is not None:
        _parse_timestamp(soak.get("started_at"), "soak started_at")
    if soak.get("completed_at") is not None:
        _parse_timestamp(soak.get("completed_at"), "soak completed_at")
    samples = soak.get("samples")
    if not isinstance(samples, list) or len(samples) > MAX_SOAK_SAMPLES:
        raise ReleaseCandidateError("soak samples are invalid")
    for sample in samples:
        if not isinstance(sample, dict):
            raise ReleaseCandidateError("soak sample must be an object")
        _parse_timestamp(sample.get("captured_at"), "sample captured_at")
        failures = validate_sample(
            sample, str(payload.get("candidate")), str(payload.get("expected_commit"))
        )
        if failures:
            raise ReleaseCandidateError(
                "stored soak sample is not healthy: " + "; ".join(failures)
            )
    sample_times = [
        _parse_timestamp(sample.get("captured_at"), "sample captured_at")
        for sample in samples
    ]
    if sample_times != sorted(sample_times):
        raise ReleaseCandidateError("soak samples are not in timestamp order")
    signoff = payload.get("signoff")
    if signoff is not None:
        if not isinstance(signoff, dict):
            raise ReleaseCandidateError("signoff must be an object")
        _exact_fields(signoff, {"operator", "signed_at"}, "signoff")
        _bounded_text(signoff.get("operator"), "operator", MAX_OPERATOR_CHARS)
        _parse_timestamp(signoff.get("signed_at"), "signoff signed_at")
    if payload.get("status") == "passed":
        if any(record.get("status") != "passed" for record in checks.values()):
            raise ReleaseCandidateError("final evidence contains an unpassed check")
        if soak.get("status") != "passed" or signoff is None:
            raise ReleaseCandidateError("final evidence has no completed soak/signoff")
        if len(samples) < MIN_SOAK_SAMPLES:
            raise ReleaseCandidateError("final evidence has too few soak samples")
        started = _parse_timestamp(soak.get("started_at"), "soak started_at")
        completed = _parse_timestamp(soak.get("completed_at"), "soak completed_at")
        if (completed - started).total_seconds() < required_seconds:
            raise ReleaseCandidateError("final evidence soak duration is too short")
        if not _has_mid_soak_sample(sample_times, started, required_seconds):
            raise ReleaseCandidateError("final evidence has no mid-soak sample")
        restart_counts = {
            sample.get("service", {}).get("NRestarts") for sample in samples
        }
        if len(restart_counts) != 1:
            raise ReleaseCandidateError("final evidence restart count changed during soak")
    elif signoff is not None or soak.get("status") == "passed":
        raise ReleaseCandidateError("in-progress evidence cannot contain final signoff")
    if soak.get("status") == "pending" and (
        soak.get("started_at") is not None
        or soak.get("completed_at") is not None
        or samples
    ):
        raise ReleaseCandidateError("pending soak cannot contain timestamps or samples")
    if soak.get("status") == "running" and (
        soak.get("started_at") is None
        or soak.get("completed_at") is not None
        or not samples
    ):
        raise ReleaseCandidateError("running soak has inconsistent state")
    return dict(payload)


def load_evidence(path: Path) -> dict[str, Any]:
    return validate_evidence(_read_json(path, "acceptance evidence", MAX_EVIDENCE_BYTES))


def save_evidence(path: Path, payload: Mapping[str, Any]) -> None:
    validated = validate_evidence(payload)
    if path.exists() and path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise ReleaseCandidateError("existing acceptance evidence is oversized")
    _atomic_json(path, validated)


def record_check(
    payload: dict[str, Any],
    identifier: str,
    result: str,
    note: str,
    *,
    now: Callable[[], str] = utc_now,
) -> None:
    if payload.get("status") == "passed":
        raise ReleaseCandidateError("finalized evidence cannot be changed")
    if identifier not in CHECK_BY_ID:
        raise ReleaseCandidateError(f"unknown acceptance check: {identifier}")
    normalized = str(result or "").strip().lower()
    if normalized not in ("passed", "failed"):
        raise ReleaseCandidateError("result must be passed or failed")
    record = payload["checks"][identifier]
    if len(record["attempts"]) >= 20:
        raise ReleaseCandidateError("acceptance check has too many recorded attempts")
    record["attempts"].append(
        {"recorded_at": now(), "result": normalized, "note": _operator_note(note)}
    )
    record["status"] = normalized


def begin_soak(
    payload: dict[str, Any],
    sample: Mapping[str, Any],
    *,
    now: Callable[[], str] = utc_now,
) -> None:
    if payload.get("status") == "passed":
        raise ReleaseCandidateError("finalized evidence cannot be changed")
    if payload["soak"]["status"] != "pending":
        raise ReleaseCandidateError("soak has already started")
    missing = [
        check.identifier
        for check in ACCEPTANCE_CHECKS
        if check.prerequisite_for_soak
        and payload["checks"][check.identifier]["status"] != "passed"
    ]
    if missing:
        raise ReleaseCandidateError(
            "soak prerequisites are not passed: " + ", ".join(missing)
        )
    failures = validate_sample(sample, payload["candidate"], payload["expected_commit"])
    if failures:
        raise ReleaseCandidateError("soak baseline is not healthy: " + "; ".join(failures))
    started_at = now()
    payload["soak"].update(
        {"status": "running", "started_at": started_at, "samples": [dict(sample)]}
    )


def add_soak_sample(payload: dict[str, Any], sample: Mapping[str, Any]) -> None:
    if payload.get("status") == "passed":
        raise ReleaseCandidateError("finalized evidence cannot be changed")
    if payload["soak"]["status"] != "running":
        raise ReleaseCandidateError("soak is not running")
    if len(payload["soak"]["samples"]) >= MAX_SOAK_SAMPLES:
        raise ReleaseCandidateError("soak sample limit reached")
    failures = validate_sample(sample, payload["candidate"], payload["expected_commit"])
    if failures:
        raise ReleaseCandidateError("soak sample is not healthy: " + "; ".join(failures))
    payload["soak"]["samples"].append(dict(sample))


def finalize_evidence(
    payload: dict[str, Any],
    final_sample: Mapping[str, Any],
    operator: str,
    *,
    now: Callable[[], str] = utc_now,
) -> None:
    if payload.get("status") == "passed":
        raise ReleaseCandidateError("evidence is already finalized")
    missing = [
        identifier
        for identifier in CHECK_BY_ID
        if payload["checks"][identifier]["status"] != "passed"
    ]
    if missing:
        raise ReleaseCandidateError("acceptance checks are not passed: " + ", ".join(missing))
    soak = payload["soak"]
    failures = validate_sample(
        final_sample, payload["candidate"], payload["expected_commit"]
    )
    if failures:
        raise ReleaseCandidateError(
            "final soak sample is not healthy: " + "; ".join(failures)
        )
    proposed_samples = [*soak["samples"], dict(final_sample)]
    if len(proposed_samples) > MAX_SOAK_SAMPLES:
        raise ReleaseCandidateError("soak sample limit reached")
    completed_at = now()
    started = _parse_timestamp(soak["started_at"], "soak started_at")
    completed = _parse_timestamp(completed_at, "soak completed_at")
    elapsed = (completed - started).total_seconds()
    if elapsed < soak["required_seconds"]:
        raise ReleaseCandidateError(
            f"soak needs {soak['required_seconds'] - int(elapsed)} more seconds"
        )
    if len(proposed_samples) < MIN_SOAK_SAMPLES:
        raise ReleaseCandidateError(
            f"soak requires at least {MIN_SOAK_SAMPLES} healthy samples"
        )
    restart_counts = {
        sample.get("service", {}).get("NRestarts") for sample in proposed_samples
    }
    if len(restart_counts) != 1:
        raise ReleaseCandidateError("service restart count changed during the soak")
    timestamps = [
        _parse_timestamp(sample.get("captured_at"), "sample captured_at")
        for sample in proposed_samples
    ]
    if timestamps != sorted(timestamps):
        raise ReleaseCandidateError("soak samples are not in timestamp order")
    if not _has_mid_soak_sample(timestamps, started, soak["required_seconds"]):
        raise ReleaseCandidateError("soak requires a healthy mid-run sample")
    soak.update(
        {
            "status": "passed",
            "completed_at": completed_at,
            "samples": proposed_samples,
        }
    )
    payload["signoff"] = {
        "operator": _bounded_text(operator, "operator", MAX_OPERATOR_CHARS),
        "signed_at": completed_at,
    }
    payload["status"] = "passed"


def verify_digest(path: Path) -> str:
    payload = load_evidence(path)
    digest_path = path.with_suffix(path.suffix + ".sha256")
    if digest_path.is_symlink() or not digest_path.is_file():
        raise ReleaseCandidateError("final evidence digest is missing")
    if (
        (path.stat().st_mode & 0o777) != 0o600
        or (digest_path.stat().st_mode & 0o777) != 0o600
    ):
        raise ReleaseCandidateError("final evidence and digest must both be mode 0600")
    parts = digest_path.read_text(encoding="utf-8").strip().split()
    if len(parts) != 2 or parts[1] != path.name or not re.fullmatch(r"[a-f0-9]{64}", parts[0]):
        raise ReleaseCandidateError("final evidence digest file is invalid")
    actual = evidence_digest(path)
    if actual != parts[0]:
        raise ReleaseCandidateError("final evidence digest does not match")
    if payload.get("status") != "passed" or payload.get("signoff") is None:
        raise ReleaseCandidateError("acceptance evidence is not finalized")
    return actual
