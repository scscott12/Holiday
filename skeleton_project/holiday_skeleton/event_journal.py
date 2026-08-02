"""Bounded, privacy-safe persistence for operational diagnostic events."""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional


JOURNAL_VERSION = 1
MAX_JOURNAL_BYTES = 256 * 1024
MAX_DETAIL_CHARS = 240
MIN_RETAINED_EVENTS = 16
MAX_RETAINED_EVENTS = 512
_SAFE_TOKEN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_SESSION = re.compile(r"^[a-f0-9]{12}$")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|pass|access[_-]?token|refresh[_-]?token|token|"
    r"client[_-]?secret|secret|api[_-]?key|authorization|mqtt_pass|mqtt_user|"
    r"username|user)\s*[:=]\s*(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+")
_URL_CREDENTIALS = re.compile(
    r"(?i)([a-z][a-z0-9+.-]*://)[^/@\s:]+:[^/@\s]+@"
)


class EventJournalError(ValueError):
    """Raised when the journal cannot be validated or safely persisted."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _safe_token(value: object, label: str) -> str:
    token = str(value or "").strip().lower()
    if not _SAFE_TOKEN.fullmatch(token):
        raise EventJournalError(f"{label} must be a safe lowercase token")
    return token


def sanitize_detail(value: object) -> str:
    """Remove common credential forms and bound one system-only detail string."""

    detail = " ".join(str(value or "").replace("\x00", " ").splitlines())
    detail = _URL_CREDENTIALS.sub(r"\1[redacted]@", detail)
    detail = _BEARER_TOKEN.sub("Bearer [redacted]", detail)
    detail = _SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[redacted]", detail)
    return detail[:MAX_DETAIL_CHARS]


@dataclass(frozen=True)
class DiagnosticEvent:
    sequence: int
    timestamp: str
    severity: str
    category: str
    code: str
    source: str
    detail: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "sequence": self.sequence,
            "timestamp": self.timestamp,
            "severity": self.severity,
            "category": self.category,
            "code": self.code,
            "source": self.source,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class EventJournalSnapshot:
    events: tuple[DiagnosticEvent, ...]
    active_session: Optional[str]
    next_sequence: int
    maximum_entries: int

    @property
    def count(self) -> int:
        return len(self.events)

    @property
    def warning_count(self) -> int:
        return sum(event.severity == "warning" for event in self.events)

    @property
    def error_count(self) -> int:
        return sum(event.severity == "error" for event in self.events)

    @property
    def last_event(self) -> Optional[DiagnosticEvent]:
        return self.events[-1] if self.events else None

    def recent_payload(self, limit: int = 20) -> dict[str, Any]:
        bounded = max(1, min(50, int(limit)))
        return {
            "events": [event.to_payload() for event in self.events[-bounded:]],
            "retained": self.count,
            "maximum_entries": self.maximum_entries,
            "warning_count": self.warning_count,
            "error_count": self.error_count,
        }

    def recent_json(self, limit: int = 20) -> str:
        return json.dumps(
            self.recent_payload(limit),
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise EventJournalError(f"{label} must be an object")
    return value


def _exact_fields(value: Mapping[str, Any], expected: set[str], label: str) -> None:
    unknown = sorted(set(value) - expected)
    missing = sorted(expected - set(value))
    if unknown:
        raise EventJournalError(f"{label} has unknown fields: {', '.join(unknown)}")
    if missing:
        raise EventJournalError(f"{label} is missing fields: {', '.join(missing)}")


def _short_timestamp(value: Any, label: str) -> str:
    if not isinstance(value, str) or not 10 <= len(value) <= 64:
        raise EventJournalError(f"{label} must be a short timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise EventJournalError(f"{label} must be an ISO timestamp") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise EventJournalError(f"{label} must include a timezone")
    return value


def _event_from_payload(value: Any) -> DiagnosticEvent:
    event = _mapping(value, "journal event")
    _exact_fields(
        event,
        {"sequence", "timestamp", "severity", "category", "code", "source", "detail"},
        "journal event",
    )
    sequence = event["sequence"]
    if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence < 1:
        raise EventJournalError("journal event sequence must be a positive integer")
    severity = _safe_token(event["severity"], "journal event severity")
    if severity not in ("info", "warning", "error"):
        raise EventJournalError("journal event severity must be info, warning, or error")
    detail = event["detail"]
    if not isinstance(detail, str) or len(detail) > MAX_DETAIL_CHARS:
        raise EventJournalError(
            f"journal event detail must be at most {MAX_DETAIL_CHARS} characters"
        )
    if sanitize_detail(detail) != detail:
        raise EventJournalError("journal event detail contains unsafe credential data")
    return DiagnosticEvent(
        sequence=sequence,
        timestamp=_short_timestamp(event["timestamp"], "journal event timestamp"),
        severity=severity,
        category=_safe_token(event["category"], "journal event category"),
        code=_safe_token(event["code"], "journal event code"),
        source=_safe_token(event["source"], "journal event source"),
        detail=detail,
    )


class EventJournal:
    """Atomically retain a small set of non-conversation operational events."""

    def __init__(
        self,
        path: os.PathLike[str] | str,
        maximum_entries: int = 128,
    ) -> None:
        requested = int(maximum_entries)
        self.maximum_entries = max(
            MIN_RETAINED_EVENTS,
            min(MAX_RETAINED_EVENTS, requested),
        )
        self.path = Path(path)
        self._events: list[DiagnosticEvent] = []
        self._active_session: Optional[str] = None
        self._next_sequence = 1
        self._loaded = False
        self._lock = threading.RLock()

    def _payload(self, events: list[DiagnosticEvent]) -> dict[str, Any]:
        return {
            "version": JOURNAL_VERSION,
            "updated_at": _utc_now(),
            "active_session": self._active_session,
            "next_sequence": self._next_sequence,
            "events": [event.to_payload() for event in events],
        }

    def _encode_bounded(self) -> tuple[bytes, list[DiagnosticEvent]]:
        retained = list(self._events[-self.maximum_entries :])
        while True:
            encoded = (
                json.dumps(self._payload(retained), indent=2, sort_keys=True) + "\n"
            ).encode("utf-8")
            if len(encoded) <= MAX_JOURNAL_BYTES:
                return encoded, retained
            if not retained:
                raise EventJournalError(
                    f"journal metadata exceed {MAX_JOURNAL_BYTES} bytes"
                )
            retained.pop(0)

    def _persist(self) -> None:
        encoded, retained = self._encode_bounded()
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
            directory_fd = os.open(
                parent,
                os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
            )
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            raise EventJournalError(f"cannot write diagnostic journal: {error}") from error
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except FileNotFoundError:
                    pass
                except OSError:
                    pass
        self._events = retained

    def load(self) -> EventJournalSnapshot:
        with self._lock:
            try:
                with self.path.open("rb") as stream:
                    raw = stream.read(MAX_JOURNAL_BYTES + 1)
            except FileNotFoundError:
                self._events = []
                self._active_session = None
                self._next_sequence = 1
                self._loaded = True
                return self.snapshot()
            except OSError as error:
                raise EventJournalError(f"cannot read diagnostic journal: {error}") from error
            if len(raw) > MAX_JOURNAL_BYTES:
                raise EventJournalError(
                    f"diagnostic journal exceeds {MAX_JOURNAL_BYTES} bytes"
                )
            try:
                payload = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise EventJournalError(f"cannot parse diagnostic journal: {error}") from error
            root = _mapping(payload, "diagnostic journal")
            _exact_fields(
                root,
                {"version", "updated_at", "active_session", "next_sequence", "events"},
                "diagnostic journal",
            )
            if isinstance(root["version"], bool) or root["version"] != JOURNAL_VERSION:
                raise EventJournalError(
                    f"unsupported diagnostic journal version {root['version']!r}"
                )
            _short_timestamp(root["updated_at"], "diagnostic journal updated_at")
            session = root["active_session"]
            if session is not None and (
                not isinstance(session, str) or not _SAFE_SESSION.fullmatch(session)
            ):
                raise EventJournalError("active_session is invalid")
            next_sequence = root["next_sequence"]
            if (
                isinstance(next_sequence, bool)
                or not isinstance(next_sequence, int)
                or next_sequence < 1
            ):
                raise EventJournalError("next_sequence must be a positive integer")
            values = root["events"]
            if not isinstance(values, list):
                raise EventJournalError("events must be an array")
            if len(values) > MAX_RETAINED_EVENTS:
                raise EventJournalError(
                    f"journal contains more than {MAX_RETAINED_EVENTS} events"
                )
            events = [_event_from_payload(value) for value in values]
            sequences = [event.sequence for event in events]
            if sequences != sorted(set(sequences)):
                raise EventJournalError("journal event sequences must be unique and ordered")
            if sequences and next_sequence <= sequences[-1]:
                raise EventJournalError("next_sequence must follow retained events")
            self._events = events[-self.maximum_entries :]
            self._active_session = session
            self._next_sequence = next_sequence
            self._loaded = True
            return self.snapshot()

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def snapshot(self) -> EventJournalSnapshot:
        with self._lock:
            return EventJournalSnapshot(
                events=tuple(self._events),
                active_session=self._active_session,
                next_sequence=self._next_sequence,
                maximum_entries=self.maximum_entries,
            )

    def _new_event(
        self,
        category: object,
        code: object,
        severity: object,
        source: object,
        detail: object = "",
    ) -> DiagnosticEvent:
        normalized_severity = _safe_token(severity, "event severity")
        if normalized_severity not in ("info", "warning", "error"):
            raise EventJournalError("event severity must be info, warning, or error")
        event = DiagnosticEvent(
            sequence=self._next_sequence,
            timestamp=_utc_now(),
            severity=normalized_severity,
            category=_safe_token(category, "event category"),
            code=_safe_token(code, "event code"),
            source=_safe_token(source, "event source"),
            detail=sanitize_detail(detail),
        )
        self._next_sequence += 1
        return event

    def _commit_events(self, additions: list[DiagnosticEvent]) -> EventJournalSnapshot:
        old_events = self._events
        old_next_sequence = self._next_sequence - len(additions)
        self._events = [*self._events, *additions]
        try:
            self._persist()
        except Exception:
            self._events = old_events
            self._next_sequence = old_next_sequence
            raise
        return self.snapshot()

    def start_session(self, source: object = "runtime") -> EventJournalSnapshot:
        with self._lock:
            self._ensure_loaded()
            normalized_source = _safe_token(source, "event source")
            previous_session = self._active_session
            self._active_session = uuid.uuid4().hex[:12]
            additions: list[DiagnosticEvent] = []
            if previous_session is not None:
                additions.append(
                    self._new_event(
                        "runtime",
                        "unclean_restart",
                        "warning",
                        normalized_source,
                        "previous runtime session did not close cleanly",
                    )
                )
            additions.append(
                self._new_event(
                    "runtime",
                    "runtime_started",
                    "info",
                    normalized_source,
                    "diagnostic session opened",
                )
            )
            try:
                return self._commit_events(additions)
            except Exception:
                self._active_session = previous_session
                raise

    def end_session(self, source: object = "runtime") -> EventJournalSnapshot:
        with self._lock:
            self._ensure_loaded()
            if self._active_session is None:
                return self.snapshot()
            previous_session = self._active_session
            event = self._new_event(
                "runtime",
                "runtime_stopped",
                "info",
                source,
                "diagnostic session closed cleanly",
            )
            self._active_session = None
            try:
                return self._commit_events([event])
            except Exception:
                self._active_session = previous_session
                raise

    def record(
        self,
        category: object,
        code: object,
        severity: object = "info",
        source: object = "runtime",
        detail: object = "",
    ) -> EventJournalSnapshot:
        with self._lock:
            self._ensure_loaded()
            event = self._new_event(category, code, severity, source, detail)
            return self._commit_events([event])
