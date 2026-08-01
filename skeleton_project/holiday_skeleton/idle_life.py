"""Low-priority, interruptible behavior scheduling while the skeleton is idle."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Optional


class IdleAction(str, Enum):
    """Small actions that make an armed skeleton feel less static."""

    EYE_PULSE = "eye_pulse"
    JAW_TWITCH = "jaw_twitch"
    MUTTER = "mutter"


@dataclass(frozen=True)
class IdleDecision:
    """One scheduled idle action and its optional spoken text."""

    action: IdleAction
    text: str = ""


class IdleLifeScheduler:
    """Choose sparse idle actions without owning hardware or a worker thread.

    ``poll`` is intended to run on the serialized controller thread.  It only
    returns a decision; the caller remains the sole owner of eyes, jaw, and
    speech.  Disarming or normal foreground work calls ``snooze`` so a fresh
    quiet interval always follows visitor activity.
    """

    def __init__(
        self,
        minimum_interval: float = 18.0,
        maximum_interval: float = 45.0,
        mutter_chance: float = 0.12,
        mutter_lines: Iterable[str] = (),
        enabled: bool = True,
        rng: Optional[Any] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        minimum = max(0.1, float(minimum_interval))
        maximum = max(minimum, float(maximum_interval))
        self.minimum_interval = minimum
        self.maximum_interval = maximum
        self.mutter_chance = min(1.0, max(0.0, float(mutter_chance)))
        self.mutter_lines = tuple(dict.fromkeys(
            line for line in (str(value).strip() for value in mutter_lines) if line
        ))
        self.enabled = bool(enabled)
        self.rng = rng if rng is not None else random.Random()
        self.clock = clock
        self._next_due: Optional[float] = None
        self._armed = False

    @property
    def next_due(self) -> Optional[float]:
        return self._next_due

    def _schedule(self, now: float) -> None:
        delay = self.rng.uniform(self.minimum_interval, self.maximum_interval)
        self._next_due = now + max(0.1, float(delay))

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self._armed = False
        self._next_due = None

    def snooze(self, now: Optional[float] = None) -> None:
        """Restart the quiet interval after foreground activity."""

        if not self.enabled:
            self._next_due = None
            return
        current = self.clock() if now is None else float(now)
        self._armed = True
        self._schedule(current)

    def poll(
        self,
        armed: bool,
        now: Optional[float] = None,
    ) -> Optional[IdleDecision]:
        """Return one due action, or ``None`` while disabled/disarmed/waiting."""

        current = self.clock() if now is None else float(now)
        armed = bool(armed)
        if not self.enabled or not armed:
            self._armed = False
            self._next_due = None
            return None

        if not self._armed or self._next_due is None:
            self._armed = True
            self._schedule(current)
            return None

        if current < self._next_due:
            return None

        self._schedule(current)
        if self.mutter_lines and self.rng.random() < self.mutter_chance:
            return IdleDecision(IdleAction.MUTTER, self.rng.choice(self.mutter_lines))
        return IdleDecision(
            self.rng.choice((IdleAction.EYE_PULSE, IdleAction.JAW_TWITCH))
        )
