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
    SET_IDLE_LIFE_ENABLED = "set_idle_life_enabled"
    SET_NIGHT_MODE = "set_night_mode"
    SET_PERSONALITY = "set_personality"
    PLAY_SCENE = "play_scene"
    STOP_SCENE = "stop_scene"
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
    SCENE = "scene"
    IDLE_LIFE = "idle_life"
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
        idle_handler: Optional[Callable[[threading.Event], None]] = None,
        heartbeat: Optional[Callable[[RuntimeState], None]] = None,
    ) -> None:
        self._handler = handler
        self._state_changed = state_changed
        self._idle_handler = idle_handler
        self._heartbeat = heartbeat
        self._queue: queue.Queue[Event] = queue.Queue(maxsize=max_queue_size)
        self._stop_event = threading.Event()
        self._idle_interrupt = threading.Event()
        self._scene_interrupt = threading.Event()
        self._pending_trigger = False
        self._pending_lock = threading.Lock()
        self._state = RuntimeState.STARTING

    @property
    def state(self) -> RuntimeState:
        return self._state

    @property
    def stop_event(self) -> threading.Event:
        return self._stop_event

    @property
    def idle_interrupt_event(self) -> threading.Event:
        return self._idle_interrupt

    @property
    def scene_interrupt_event(self) -> threading.Event:
        return self._scene_interrupt

    def interrupt_idle(self) -> None:
        """Ask an in-progress idle behavior to yield without queuing work."""

        self._idle_interrupt.set()

    def interrupt_scene(self) -> None:
        """Ask an in-progress scene to yield without touching hardware."""

        self._scene_interrupt.set()

    def set_state(self, state: RuntimeState) -> None:
        changed = state != self._state
        if changed:
            self._state = state
            if self._state_changed is not None:
                self._state_changed(state)
        self.heartbeat()

    def heartbeat(self) -> None:
        """Report progress without allowing watchdog failures to stop work."""

        if self._heartbeat is not None:
            try:
                self._heartbeat(self._state)
            except Exception:
                pass

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

        self.interrupt_idle()
        self.interrupt_scene()

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
        self.interrupt_idle()
        self.interrupt_scene()
        try:
            self._queue.put_nowait(Event(EventKind.SHUTDOWN, source=source))
        except queue.Full:
            # The stop flag is authoritative; this event only wakes an idle loop.
            pass

    def run_forever(self) -> None:
        self.set_state(RuntimeState.IDLE)
        while not self._stop_event.is_set():
            self.heartbeat()
            if self._state is RuntimeState.IDLE:
                self._idle_interrupt.clear()
            try:
                event = self._queue.get(timeout=0.25)
            except queue.Empty:
                if (
                    self._idle_handler is not None
                    and self._state is RuntimeState.IDLE
                    and not self._idle_interrupt.is_set()
                    and not self._stop_event.is_set()
                ):
                    try:
                        self.heartbeat()
                        self._idle_handler(self._idle_interrupt)
                        self.heartbeat()
                    except Exception:
                        self.set_state(RuntimeState.ERROR)
                        raise
                continue

            if event.kind is EventKind.SHUTDOWN:
                self._queue.task_done()
                break

            if event.kind is EventKind.PLAY_SCENE:
                # The PLAY_SCENE enqueue wakes a prior scene too. Clear that
                # signal only when this scene reaches the head of the queue.
                # A later queued command must still make the new scene yield.
                self._scene_interrupt.clear()
                if not self._queue.empty():
                    self._scene_interrupt.set()

            try:
                self.heartbeat()
                self._handler(event)
                self.heartbeat()
            except Exception:
                self.set_state(RuntimeState.ERROR)
                raise
            finally:
                if event.kind is EventKind.TRIGGER:
                    with self._pending_lock:
                        self._pending_trigger = False
                self._queue.task_done()

        self.set_state(RuntimeState.STOPPING)
