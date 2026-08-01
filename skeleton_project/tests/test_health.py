import json
import threading
import time
import unittest

from holiday_skeleton.health import (
    ComponentState,
    HealthState,
    RuntimeHealthMonitor,
    SystemTelemetry,
    decode_throttled,
)


class RuntimeHealthMonitorTests(unittest.TestCase):
    def monitor(self, telemetry=None, latency_window=20):
        published = []
        monitor = RuntimeHealthMonitor(
            publisher=published.append,
            telemetry_reader=lambda: telemetry or SystemTelemetry(),
            latency_window=latency_window,
        )
        return monitor, published

    def test_ready_components_are_healthy_and_disabled_features_are_neutral(self):
        monitor, _ = self.monitor()
        monitor.set_component("runtime", ComponentState.READY, critical=True, publish=False)
        monitor.set_component("barge_in", ComponentState.DISABLED, publish=False)

        snapshot = monitor.publish_now()

        self.assertEqual(snapshot.state, HealthState.HEALTHY)
        self.assertTrue(snapshot.operational)
        self.assertEqual(snapshot.reasons, ())

    def test_optional_failure_degrades_but_remains_operational(self):
        monitor, _ = self.monitor()
        monitor.set_component("runtime", ComponentState.READY, critical=True, publish=False)
        monitor.set_component(
            "motion", ComponentState.FAILED, "PIR unavailable", critical=False,
            publish=False,
        )

        snapshot = monitor.publish_now()

        self.assertEqual(snapshot.state, HealthState.DEGRADED)
        self.assertTrue(snapshot.operational)
        self.assertEqual(snapshot.reasons, ("motion: PIR unavailable",))

    def test_critical_failure_is_unhealthy(self):
        monitor, _ = self.monitor()
        monitor.set_component(
            "speech", ComponentState.FAILED, "no Piper path", critical=True,
            publish=False,
        )

        snapshot = monitor.publish_now()

        self.assertEqual(snapshot.state, HealthState.UNHEALTHY)
        self.assertFalse(snapshot.operational)

    def test_starting_and_stopping_take_precedence(self):
        monitor, _ = self.monitor()
        monitor.set_component("runtime", ComponentState.STARTING, critical=True, publish=False)
        self.assertEqual(monitor.snapshot().state, HealthState.STARTING)

        monitor.set_component("runtime", ComponentState.STOPPING, critical=True, publish=False)
        self.assertEqual(monitor.snapshot().state, HealthState.STOPPING)

    def test_latency_window_reports_average_p95_latest_and_count(self):
        monitor, _ = self.monitor(latency_window=3)
        for value in (1, 2, 3, 4, 5):
            monitor.record_latency("response_first_audio", value)

        summary = monitor.snapshot().latencies["response_first_audio"]

        self.assertEqual(summary.count, 3)
        self.assertEqual(summary.average, 4.0)
        self.assertEqual(summary.p95, 5.0)
        self.assertEqual(summary.latest, 5.0)

    def test_counters_accumulate_and_ignore_invalid_latency(self):
        monitor, _ = self.monitor()
        monitor.increment("audio_dropped_frames")
        monitor.increment("audio_dropped_frames", 3)
        monitor.record_latency("bad", -1)

        snapshot = monitor.snapshot()

        self.assertEqual(snapshot.counters["audio_dropped_frames"], 4)
        self.assertNotIn("bad", snapshot.latencies)

    def test_temperature_throttling_and_disk_are_evaluated(self):
        telemetry = SystemTelemetry(
            cpu_temperature_c=76.5,
            disk_percent=91.0,
            throttled_raw=0x4,
            throttle_flags=("throttled_now",),
        )
        monitor, _ = self.monitor(telemetry)
        monitor.set_component("runtime", ComponentState.READY, critical=True, publish=False)

        snapshot = monitor.publish_now()

        self.assertEqual(snapshot.state, HealthState.DEGRADED)
        self.assertIn("CPU temperature 76.5 C", snapshot.components["system"].detail)
        self.assertIn("disk 91.0% used", snapshot.components["system"].detail)
        self.assertTrue(snapshot.telemetry.currently_throttled)

    def test_critical_temperature_marks_system_failed(self):
        monitor, _ = self.monitor(SystemTelemetry(cpu_temperature_c=83.0))
        monitor.set_component("runtime", ComponentState.READY, critical=True, publish=False)

        snapshot = monitor.publish_now()

        self.assertEqual(snapshot.components["system"].state, ComponentState.FAILED)
        self.assertEqual(snapshot.state, HealthState.UNHEALTHY)

    def test_probe_updates_component_without_crashing_monitor(self):
        monitor, _ = self.monitor()
        monitor.set_component("runtime", ComponentState.READY, critical=True, publish=False)
        monitor.add_probe(
            "ollama",
            lambda: (ComponentState.READY, "model responding"),
        )

        snapshot = monitor.publish_now()

        self.assertEqual(snapshot.components["ollama"].state, ComponentState.READY)
        self.assertEqual(snapshot.components["ollama"].detail, "model responding")

    def test_unchanged_component_does_not_republish_health(self):
        monitor, published = self.monitor()
        monitor.set_component("speech", ComponentState.READY, "streaming")
        monitor.set_component("speech", ComponentState.READY, "streaming")

        self.assertEqual(len(published), 1)

    def test_component_attributes_are_valid_json(self):
        monitor, _ = self.monitor()
        monitor.set_component(
            "microphone", ComponentState.FAILED, "input missing", publish=False
        )

        attributes = json.loads(monitor.snapshot().component_attributes_json())

        self.assertEqual(attributes["microphone"]["state"], "failed")
        self.assertEqual(attributes["microphone"]["detail"], "input missing")

    def test_decode_throttled_includes_current_and_historical_bits(self):
        flags = decode_throttled((1 << 0) | (1 << 18))

        self.assertEqual(flags, ("under_voltage_now", "throttled_occurred"))

    def test_start_does_not_block_on_slow_telemetry_or_probe(self):
        reading = threading.Event()
        release = threading.Event()
        published = threading.Event()

        def slow_reader():
            reading.set()
            release.wait(timeout=1)
            return SystemTelemetry()

        monitor = RuntimeHealthMonitor(
            publisher=lambda _snapshot: published.set(),
            telemetry_reader=slow_reader,
            interval_seconds=60,
        )
        started_at = time.monotonic()

        monitor.start()

        self.assertLess(time.monotonic() - started_at, 0.1)
        self.assertTrue(reading.wait(timeout=1))
        release.set()
        self.assertTrue(published.wait(timeout=1))
        monitor.stop()


if __name__ == "__main__":
    unittest.main()
