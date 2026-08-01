"""Serialized event controller for the skeleton runtime.

MQTT and GPIO callbacks may run on different threads.  They must never operate
speech or animation hardware directly.  This controller gives the application
one owner for those side effects while keeping everything in a single process.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Optional


class EventKind(str, Enum):
    TRIGGER = "trigger"
    SAY = "say"
    BLINK = "blink"
    FLICKER = "flicker"
    SET_EYES_DIM = "set_eyes_dim"
    SET_EYES_FULL = "set_eyes_full"
    SET_VOLUME = "set_volume"
    SET_MOTION_ENABLED = "set_motion_enabled"
    SET_NIGHT_MODE = "set_night_mode"
    RESTART = "restart"
    SHUTDOWN = "shutdown"


class RuntimeState(str, Enum):
    STARTING = "starting"
    IDLE = "idle"
    GREETING = "greeting"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    EFFECT = "effect"
    COOLDOWN = "cooldown"
    STOPPING = "stopping"
    ERROR = "error"


@dataclass(frozen=True)
class Event:
    kind: EventKind
    payload: Any = None
    source: str = "runtime"


class SkeletonController:
    """Process events one at a time on the thread calling ``run_forever``."""

    def __init__(
        self,
        handler: Callable[[Event], None],
        state_changed: Optional[Callable[[RuntimeState], None]] = None,
        max_queue_size: int = 64,
    ) -> None:
        self._handler = handler
        self._state_changed = state_changed
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._pending_trigger = False
        self._pending_lock = threading.Lock()
        self._state = RuntimeState.STARTING

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    def set_state(self, state: RuntimeState) -> None:
        if state == self._state:
            return
        self._state = state
        if self._state_changed is not None:
            self._state_changed(state)

    def enqueue(
        self,
        kind: EventKind,
        payload: Any = None,
        source: str = "runtime",
    ) -> bool:
        """Queue an event without blocking a GPIO or MQTT callback.

        Motion/manual triggers are coalesced while a conversation is queued or
        active.  This prevents a visitor from building up several conversations
        while the skeleton is already speaking.
        """

        if self._stop_event.is_set() and kind is not EventKind.SHUTDOWN:
            return False

        if kind is EventKind.TRIGGER:
            with self._pending_lock:
                if self._pending_trigger:
                    return False
                self._pending_trigger = True

        try:
            self._queue.put_nowait(Event(kind, payload, source))
            return True
        except queue.Full:
            if kind is EventKind.TRIGGER:
                with self._pending_lock:
                    self._pending_trigger = False
            return False

    def request_stop(self, source: str = "runtime") -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(Event(EventKind.SHUTDOWN, source=source))
        except queue.Full:
            # The stop flag is authoritative; this event only wakes an idle loop.
            pass

    def run_forever(self) -> None:
        self.set_state(RuntimeState.IDLE)
        while not self._stop_event.is_set():
            try:
                event = self._queue.get(timeout=0.25)
            except queue.Empty:
                continue

            if event.kind is EventKind.SHUTDOWN:
                self._queue.task_done()
                break

            try:
                self._handler(event)
            except Exception:
                self.set_state(RuntimeState.ERROR)
                raise
            finally:
                if event.kind is EventKind.TRIGGER:
                    with self._pending_lock:
                        self._pending_trigger = False
                self._queue.task_done()

        self.set_state(RuntimeState.STOPPING)

