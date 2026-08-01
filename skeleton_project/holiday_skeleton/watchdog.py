"""Dependency-free systemd readiness and controller watchdog notifications."""

from __future__ import annotations

import os
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Mapping, Optional, Union


SocketAddress = Union[str, bytes]
DatagramSender = Callable[[SocketAddress, bytes], None]


class WatchdogState(str, Enum):
    DISABLED = "disabled"
    STARTING = "starting"
    READY = "ready"
    STALE = "stale"
    ERROR = "error"
    STOPPING = "stopping"


@dataclass(frozen=True)
class WatchdogSnapshot:
    enabled: bool
    state: WatchdogState
    controller_age_seconds: Optional[float]
    timeout_seconds: Optional[float]
    stale_after_seconds: Optional[float]
    feed_count: int
    last_feed: str
    last_error: str
    controller_state: str


def _default_sender(address: SocketAddress, payload: bytes) -> None:
    with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
        channel.connect(address)
        channel.sendall(payload)


def _socket_address(value: str) -> SocketAddress:
    if value.startswith("@"):
        return b"\0" + value[1:].encode("utf-8")
    return value


def _short_status(value: object) -> str:
    return " ".join(str(value).replace("\x00", " ").splitlines())[:240]


class SystemdNotifier:
    """Send the small subset of ``sd_notify`` messages used by the runtime."""

    def __init__(
        self,
        notify_socket: str = "",
        watchdog_timeout_seconds: Optional[float] = None,
        sender: DatagramSender = _default_sender,
    ) -> None:
        self.notify_socket = str(notify_socket or "").strip()
        timeout = (
            None
            if watchdog_timeout_seconds is None
            else float(watchdog_timeout_seconds)
        )
        self.watchdog_timeout_seconds = timeout if timeout and timeout > 0 else None
        self.sender = sender
        self.last_error = ""

    @classmethod
    def from_environment(
        cls,
        environ: Optional[Mapping[str, str]] = None,
        sender: DatagramSender = _default_sender,
        pid: Optional[int] = None,
    ) -> "SystemdNotifier":
        values = os.environ if environ is None else environ
        notify_socket = str(values.get("NOTIFY_SOCKET", "") or "").strip()
        timeout: Optional[float] = None
        try:
            watchdog_usec = int(str(values.get("WATCHDOG_USEC", "0") or "0"))
        except ValueError:
            watchdog_usec = 0

        process_id = os.getpid() if pid is None else int(pid)
        watchdog_pid_raw = str(values.get("WATCHDOG_PID", "") or "").strip()
        try:
            watchdog_pid = int(watchdog_pid_raw) if watchdog_pid_raw else process_id
        except ValueError:
            watchdog_pid = -1
        if notify_socket and watchdog_usec > 0 and watchdog_pid == process_id:
            timeout = watchdog_usec / 1_000_000.0
        return cls(notify_socket, timeout, sender)

    @property
    def notifications_available(self) -> bool:
        return bool(self.notify_socket)

    @property
    def watchdog_enabled(self) -> bool:
        return bool(self.notifications_available and self.watchdog_timeout_seconds)

    def send(self, *assignments: str) -> bool:
        if not self.notifications_available:
            return False
        fields = [str(item).strip() for item in assignments if str(item).strip()]
        if not fields:
            return False
        if any("=" not in item or "\n" in item or "\x00" in item for item in fields):
            self.last_error = "invalid systemd notification field"
            return False
        try:
            self.sender(
                _socket_address(self.notify_socket),
                "\n".join(fields).encode("utf-8"),
            )
        except (OSError, ValueError, TypeError) as error:
            self.last_error = _short_status(error) or "notification failed"
            return False
        self.last_error = ""
        return True

    def status(self, message: object) -> bool:
        return self.send(f"STATUS={_short_status(message)}")

    def ready(self, message: object = "controller ready") -> bool:
        return self.send("READY=1", f"STATUS={_short_status(message)}")

    def watchdog(self) -> bool:
        return self.send("WATCHDOG=1")

    def stopping(self, message: object = "service stopping") -> bool:
        return self.send("STOPPING=1", f"STATUS={_short_status(message)}")


WatchdogChanged = Callable[[WatchdogSnapshot], None]


class ControllerWatchdog:
    """Feed systemd only while the serialized controller is making progress."""

    def __init__(
        self,
        notifier: SystemdNotifier,
        stale_after_seconds: Optional[float] = None,
        changed: Optional[WatchdogChanged] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.notifier = notifier
        self.changed = changed
        self.clock = clock
        timeout = notifier.watchdog_timeout_seconds
        requested = float(stale_after_seconds or 0.0)
        if timeout is None:
            self.stale_after_seconds = None
            self.feed_interval_seconds = None
            state = WatchdogState.DISABLED
        else:
            default_stale = timeout * 0.75
            self.stale_after_seconds = min(
                max(0.1, requested if requested > 0 else default_stale),
                max(0.1, timeout * 0.9),
            )
            self.feed_interval_seconds = max(0.05, timeout / 3.0)
            state = WatchdogState.STARTING
        self._state = state
        self._last_controller_pulse: Optional[float] = None
        self._controller_state = "starting"
        self._feed_count = 0
        self._last_feed = "never"
        self._last_error = ""
        self._ready_announced = False
        self._ready_message = "controller ready"
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    @classmethod
    def from_environment(
        cls,
        stale_after_seconds: Optional[float] = None,
        changed: Optional[WatchdogChanged] = None,
        environ: Optional[Mapping[str, str]] = None,
        sender: DatagramSender = _default_sender,
    ) -> "ControllerWatchdog":
        return cls(
            SystemdNotifier.from_environment(environ=environ, sender=sender),
            stale_after_seconds=stale_after_seconds,
            changed=changed,
        )

    @property
    def enabled(self) -> bool:
        return self.notifier.watchdog_enabled

    def pulse(self, controller_state: object = "running") -> None:
        with self._lock:
            self._last_controller_pulse = self.clock()
            self._controller_state = _short_status(controller_state) or "running"

    def snapshot(self) -> WatchdogSnapshot:
        with self._lock:
            pulse = self._last_controller_pulse
            age = None if pulse is None else max(0.0, self.clock() - pulse)
            return WatchdogSnapshot(
                enabled=self.enabled,
                state=self._state,
                controller_age_seconds=age,
                timeout_seconds=self.notifier.watchdog_timeout_seconds,
                stale_after_seconds=self.stale_after_seconds,
                feed_count=self._feed_count,
                last_feed=self._last_feed,
                last_error=self._last_error,
                controller_state=self._controller_state,
            )

    def _emit(self) -> None:
        callback = self.changed
        if callback is not None:
            try:
                callback(self.snapshot())
            except Exception:
                # Watchdog reporting must never break watchdog feeding.
                pass

    def feed_once(self) -> bool:
        if not self.enabled or self._stop_event.is_set():
            return False
        with self._lock:
            pulse = self._last_controller_pulse
            age = None if pulse is None else max(0.0, self.clock() - pulse)
            if age is None:
                self._state = WatchdogState.STARTING
                self._last_error = "waiting for controller heartbeat"
                should_feed = False
            elif age > float(self.stale_after_seconds or 0.0):
                self._state = WatchdogState.STALE
                self._last_error = f"controller heartbeat stale for {age:.1f}s"
                should_feed = False
            else:
                should_feed = True

        if should_feed:
            with self._lock:
                ready_announced = self._ready_announced
                ready_message = self._ready_message
            if not ready_announced:
                ready_sent = self.notifier.ready(ready_message)
                with self._lock:
                    self._ready_announced = ready_sent
                    if not ready_sent:
                        self._last_error = (
                            self.notifier.last_error or "systemd ready notify failed"
                        )
                        self._state = WatchdogState.ERROR
                if not ready_sent:
                    self._emit()
                    return False
            sent = self.notifier.watchdog()
            with self._lock:
                if sent:
                    self._feed_count += 1
                    self._last_feed = datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    )
                    self._last_error = ""
                    self._state = WatchdogState.READY
                else:
                    self._last_error = self.notifier.last_error or "watchdog notify failed"
                    self._state = WatchdogState.ERROR
            self._emit()
            return sent

        self._emit()
        return False

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self.feed_once()
            if self._stop_event.wait(float(self.feed_interval_seconds or 1.0)):
                break

    def ready(self, message: object = "controller ready") -> bool:
        ready_message = _short_status(message) or "controller ready"
        sent = self.notifier.ready(ready_message)
        with self._lock:
            self._ready_message = ready_message
            self._ready_announced = sent
            if not sent and self.enabled:
                self._last_error = self.notifier.last_error or "systemd ready notify failed"
                self._state = WatchdogState.ERROR
        if not sent and self.enabled:
            self._emit()
        return sent

    def start(self) -> None:
        if not self.enabled:
            self._emit()
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="skeleton-watchdog",
            daemon=True,
        )
        self._thread.start()

    def stop(self, message: object = "service stopping") -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=min(2.0, float(self.feed_interval_seconds or 1.0) + 0.1))
        self._thread = None
        with self._lock:
            self._state = WatchdogState.STOPPING
        self.notifier.stopping(message)
        self._emit()
