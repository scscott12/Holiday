"""Bounded, interruptible operator self-tests for installed hardware."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Optional, Tuple


class SelfTestStepStatus(str, Enum):
    PASSED = "passed"
    SKIPPED = "skipped"
    FAILED = "failed"


class SelfTestStepSkipped(RuntimeError):
    """Raised when an optional test step cannot run on this installation."""


class SelfTestInterrupted(RuntimeError):
    """Raised by a step when foreground work or barge-in stops the test."""


@dataclass(frozen=True)
class SelfTestStep:
    name: str
    action: Callable[[Any], Optional[str]]


@dataclass(frozen=True)
class SelfTestStepResult:
    name: str
    status: SelfTestStepStatus
    detail: str


@dataclass(frozen=True)
class SelfTestResult:
    outcome: str
    steps: Tuple[SelfTestStepResult, ...]
    duration_seconds: float
    interrupted: bool = False
    timed_out: bool = False

    @property
    def error(self) -> str:
        failures = tuple(
            f"{step.name}: {step.detail}"
            for step in self.steps
            if step.status is SelfTestStepStatus.FAILED
        )
        return "; ".join(failures)

    def report_json(self) -> str:
        return json.dumps(
            {
                "outcome": self.outcome,
                "duration_seconds": round(self.duration_seconds, 3),
                "interrupted": self.interrupted,
                "timed_out": self.timed_out,
                "steps": [
                    {
                        "name": step.name,
                        "status": step.status.value,
                        "detail": step.detail,
                    }
                    for step in self.steps
                ],
            },
            separators=(",", ":"),
            sort_keys=True,
        )


class _DeadlineStopSignal:
    """Event-like view combining an external interrupt with a hard deadline."""

    def __init__(
        self,
        interrupt_event: Any,
        deadline: float,
        clock: Callable[[], float],
    ) -> None:
        self.interrupt_event = interrupt_event
        self.deadline = float(deadline)
        self.clock = clock

    @property
    def timed_out(self) -> bool:
        return self.clock() >= self.deadline

    def is_set(self) -> bool:
        return bool(self.interrupt_event.is_set() or self.timed_out)

    def wait(self, timeout: Optional[float] = None) -> bool:
        remaining = max(0.0, self.deadline - self.clock())
        duration = remaining if timeout is None else min(max(0.0, timeout), remaining)
        if duration > 0 and self.interrupt_event.wait(duration):
            return True
        return self.is_set()


class SelfTestRunner:
    """Run diagnostic steps serially without allowing them to own hardware threads."""

    def __init__(
        self,
        maximum_seconds: float = 12.0,
        progress: Optional[Callable[[int, int, SelfTestStep], None]] = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maximum_seconds = max(1.0, min(60.0, float(maximum_seconds)))
        self.progress = progress
        self.clock = clock

    def run(
        self,
        steps: Iterable[SelfTestStep],
        interrupt_event: Any,
    ) -> SelfTestResult:
        configured = tuple(steps)
        started = self.clock()
        stop_signal = _DeadlineStopSignal(
            interrupt_event,
            started + self.maximum_seconds,
            self.clock,
        )
        results = []
        interrupted = False

        for index, step in enumerate(configured, start=1):
            if stop_signal.is_set():
                interrupted = True
                break
            if self.progress is not None:
                self.progress(index, len(configured), step)
            try:
                detail = step.action(stop_signal) or "ok"
            except SelfTestStepSkipped as error:
                results.append(
                    SelfTestStepResult(
                        step.name,
                        SelfTestStepStatus.SKIPPED,
                        str(error) or "unavailable",
                    )
                )
                continue
            except SelfTestInterrupted:
                interrupted = True
                break
            except Exception as error:
                results.append(
                    SelfTestStepResult(
                        step.name,
                        SelfTestStepStatus.FAILED,
                        str(error) or type(error).__name__,
                    )
                )
                continue

            if stop_signal.is_set():
                interrupted = True
                break
            results.append(
                SelfTestStepResult(
                    step.name,
                    SelfTestStepStatus.PASSED,
                    str(detail),
                )
            )

        timed_out = stop_signal.timed_out
        if timed_out:
            interrupted = True
        if interrupted:
            outcome = "timed_out" if timed_out else "interrupted"
        elif any(item.status is SelfTestStepStatus.FAILED for item in results):
            outcome = "failed"
        elif any(item.status is SelfTestStepStatus.SKIPPED for item in results):
            outcome = "degraded"
        else:
            outcome = "passed"
        return SelfTestResult(
            outcome=outcome,
            steps=tuple(results),
            duration_seconds=max(0.0, self.clock() - started),
            interrupted=interrupted,
            timed_out=timed_out,
        )
