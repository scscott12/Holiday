"""Runtime health aggregation and low-overhead Raspberry Pi telemetry."""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from statistics import fmean
from typing import Callable, Deque, Dict, Mapping, Optional, Tuple


class ComponentState(str, Enum):
    STARTING = "starting"
    READY = "ready"
    DEGRADED = "degraded"
    FAILED = "failed"
    DISABLED = "disabled"
    STOPPING = "stopping"


class HealthState(str, Enum):
    STARTING = "starting"
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    STOPPING = "stopping"


@dataclass(frozen=True)
class ComponentHealth:
    state: ComponentState
    detail: str = ""
    critical: bool = False


@dataclass(frozen=True)
class SystemTelemetry:
    cpu_temperature_c: Optional[float] = None
    load_1m: Optional[float] = None
    memory_percent: Optional[float] = None
    disk_percent: Optional[float] = None
    uptime_seconds: Optional[float] = None
    throttled_raw: Optional[int] = None
    throttle_flags: Tuple[str, ...] = ()

    @property
    def currently_throttled(self) -> bool:
        return bool((self.throttled_raw or 0) & 0xF)


@dataclass(frozen=True)
class LatencySummary:
    count: int
    average: float
    p95: float
    latest: float


@dataclass(frozen=True)
class HealthSnapshot:
    state: HealthState
    reasons: Tuple[str, ...]
    components: Mapping[str, ComponentHealth]
    telemetry: SystemTelemetry
    latencies: Mapping[str, LatencySummary]
    counters: Mapping[str, int]
    heartbeat: int
    timestamp: str

    @property
    def operational(self) -> bool:
        return self.state not in (HealthState.UNHEALTHY, HealthState.STOPPING)

    def component_attributes_json(self) -> str:
        return json.dumps(
            {
                name: {
                    "state": component.state.value,
                    "detail": component.detail,
                    "critical": component.critical,
                }
                for name, component in sorted(self.components.items())
            },
            separators=(",", ":"),
            sort_keys=True,
        )


def _percentile_95(values: Tuple[float, ...]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(0.95 * len(ordered)) - 1)
    return float(ordered[index])


def _read_first_number(path: Path, divisor: float = 1.0) -> Optional[float]:
    try:
        return float(path.read_text(encoding="utf-8").strip().split()[0]) / divisor
    except (OSError, ValueError, IndexError):
        return None


def _memory_percent(path: Path) -> Optional[float]:
    try:
        values: Dict[str, float] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            key, value = line.split(":", 1)
            values[key] = float(value.strip().split()[0])
        total = values.get("MemTotal", 0.0)
        available = values.get("MemAvailable", 0.0)
        if total <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (total - available) / total))
    except (OSError, ValueError, IndexError):
        return None


_THROTTLE_BITS = {
    0: "under_voltage_now",
    1: "frequency_capped_now",
    2: "throttled_now",
    3: "soft_temp_limit_now",
    16: "under_voltage_occurred",
    17: "frequency_capped_occurred",
    18: "throttled_occurred",
    19: "soft_temp_limit_occurred",
}


def decode_throttled(value: int) -> Tuple[str, ...]:
    return tuple(name for bit, name in _THROTTLE_BITS.items() if value & (1 << bit))


def _read_throttled() -> Optional[int]:
    try:
        result = subprocess.run(
            ["vcgencmd", "get_throttled"],
            check=False,
            capture_output=True,
            text=True,
            timeout=2.0,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0 or "=" not in result.stdout:
        return None
    try:
        return int(result.stdout.strip().split("=", 1)[1], 0)
    except ValueError:
        return None


def read_system_telemetry(
    thermal_path: Path = Path("/sys/class/thermal/thermal_zone0/temp"),
    meminfo_path: Path = Path("/proc/meminfo"),
    uptime_path: Path = Path("/proc/uptime"),
    disk_path: str = "/",
) -> SystemTelemetry:
    """Read metrics available on a Pi without adding a Python dependency."""

    try:
        load_1m = float(os.getloadavg()[0])
    except (AttributeError, OSError):
        load_1m = None
    try:
        disk = shutil.disk_usage(disk_path)
        disk_percent = 100.0 * disk.used / disk.total if disk.total else None
    except OSError:
        disk_percent = None
    throttled = _read_throttled()
    return SystemTelemetry(
        cpu_temperature_c=_read_first_number(thermal_path, divisor=1000.0),
        load_1m=load_1m,
        memory_percent=_memory_percent(meminfo_path),
        disk_percent=disk_percent,
        uptime_seconds=_read_first_number(uptime_path),
        throttled_raw=throttled,
        throttle_flags=decode_throttled(throttled or 0),
    )


ProbeResult = Tuple[ComponentState, str]
Probe = Callable[[], ProbeResult]


class RuntimeHealthMonitor:
    """Collect component state and publish periodic immutable snapshots."""

    def __init__(
        self,
        publisher: Callable[[HealthSnapshot], None],
        interval_seconds: float = 30.0,
        latency_window: int = 20,
        telemetry_reader: Callable[[], SystemTelemetry] = read_system_telemetry,
        temperature_warning_c: float = 75.0,
        temperature_critical_c: float = 82.0,
        disk_warning_percent: float = 90.0,
        disk_critical_percent: float = 97.0,
    ) -> None:
        self.publisher = publisher
        self.interval_seconds = max(1.0, float(interval_seconds))
        self.latency_window = max(1, int(latency_window))
        self.telemetry_reader = telemetry_reader
        self.temperature_warning_c = float(temperature_warning_c)
        self.temperature_critical_c = float(temperature_critical_c)
        self.disk_warning_percent = float(disk_warning_percent)
        self.disk_critical_percent = float(disk_critical_percent)
        self._components: Dict[str, ComponentHealth] = {}
        self._latencies: Dict[str, Deque[float]] = {}
        self._counters: Dict[str, int] = {}
        self._probes: Dict[str, Tuple[Probe, bool]] = {}
        self._telemetry = SystemTelemetry()
        self._heartbeat = 0
        self._lock = threading.RLock()
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def set_component(
        self,
        name: str,
        state: ComponentState,
        detail: str = "",
        critical: bool = False,
        publish: bool = True,
    ) -> None:
        component = ComponentHealth(
            state=ComponentState(state),
            detail=str(detail),
            critical=bool(critical),
        )
        with self._lock:
            if self._components.get(str(name)) == component:
                return
            self._components[str(name)] = component
        if publish:
            self.publish_now(sample=False)

    def add_probe(self, name: str, probe: Probe, critical: bool = False) -> None:
        with self._lock:
            self._probes[str(name)] = (probe, bool(critical))

    def record_latency(self, name: str, seconds: float) -> None:
        value = float(seconds)
        if not math.isfinite(value) or value < 0:
            return
        with self._lock:
            values = self._latencies.setdefault(
                str(name), deque(maxlen=self.latency_window)
            )
            values.append(value)

    def increment(self, name: str, amount: int = 1) -> None:
        with self._lock:
            self._counters[str(name)] = self._counters.get(str(name), 0) + int(amount)

    def _run_probes(self) -> None:
        with self._lock:
            probes = tuple(self._probes.items())
        for name, (probe, critical) in probes:
            try:
                state, detail = probe()
            except Exception as error:
                state, detail = ComponentState.FAILED, str(error)
            self.set_component(name, state, detail, critical, publish=False)

    def _evaluate_system(self, telemetry: SystemTelemetry) -> None:
        reasons = []
        failed = False
        temperature = telemetry.cpu_temperature_c
        if temperature is not None:
            if temperature >= self.temperature_critical_c:
                failed = True
                reasons.append(f"CPU temperature {temperature:.1f} C")
            elif temperature >= self.temperature_warning_c:
                reasons.append(f"CPU temperature {temperature:.1f} C")
        disk = telemetry.disk_percent
        if disk is not None:
            if disk >= self.disk_critical_percent:
                failed = True
                reasons.append(f"disk {disk:.1f}% used")
            elif disk >= self.disk_warning_percent:
                reasons.append(f"disk {disk:.1f}% used")
        if telemetry.currently_throttled:
            reasons.append("Pi is currently throttled")
        state = (
            ComponentState.FAILED
            if failed
            else ComponentState.DEGRADED
            if reasons
            else ComponentState.READY
        )
        self.set_component("system", state, "; ".join(reasons), True, publish=False)

    def sample(self) -> None:
        self._run_probes()
        try:
            telemetry = self.telemetry_reader()
        except Exception as error:
            telemetry = SystemTelemetry()
            self.set_component(
                "system", ComponentState.DEGRADED, f"telemetry failed: {error}", True,
                publish=False,
            )
        else:
            self._evaluate_system(telemetry)
        with self._lock:
            self._telemetry = telemetry

    @staticmethod
    def _overall(components: Mapping[str, ComponentHealth]) -> Tuple[HealthState, Tuple[str, ...]]:
        if any(item.state is ComponentState.STOPPING for item in components.values()):
            return HealthState.STOPPING, ("runtime stopping",)
        if any(item.state is ComponentState.STARTING for item in components.values()):
            return HealthState.STARTING, ("startup checks in progress",)
        reasons = tuple(
            f"{name}: {item.detail or item.state.value}"
            for name, item in sorted(components.items())
            if item.state in (ComponentState.DEGRADED, ComponentState.FAILED)
        )
        if any(
            item.critical and item.state is ComponentState.FAILED
            for item in components.values()
        ):
            return HealthState.UNHEALTHY, reasons
        if reasons:
            return HealthState.DEGRADED, reasons
        return HealthState.HEALTHY, ()

    def snapshot(self) -> HealthSnapshot:
        with self._lock:
            components = dict(self._components)
            latencies = {
                name: LatencySummary(
                    count=len(values),
                    average=fmean(values) if values else 0.0,
                    p95=_percentile_95(tuple(values)),
                    latest=values[-1] if values else 0.0,
                )
                for name, values in self._latencies.items()
            }
            counters = dict(self._counters)
            telemetry = self._telemetry
            heartbeat = self._heartbeat
        state, reasons = self._overall(components)
        return HealthSnapshot(
            state=state,
            reasons=reasons,
            components=components,
            telemetry=telemetry,
            latencies=latencies,
            counters=counters,
            heartbeat=heartbeat,
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )

    def publish_now(self, sample: bool = True) -> HealthSnapshot:
        if sample:
            self.sample()
        with self._lock:
            self._heartbeat += 1
        snapshot = self.snapshot()
        self.publisher(snapshot)
        return snapshot

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.sample()
                if self._stop_event.is_set():
                    break
                self.publish_now(sample=False)
            except Exception:
                # Monitoring must never bring down the controller/audio process.
                pass
            if self._stop_event.wait(self.interval_seconds):
                break

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="skeleton-health",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=min(2.0, self.interval_seconds + 0.25))
        self._thread = None
