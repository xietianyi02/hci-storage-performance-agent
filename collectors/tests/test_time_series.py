"""Offline lifecycle/window tests; no Linux workload, perf, or cluster is run."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.__main__ import parser
from collectors.capture import collect, sample_deadlines
from collectors.merge import build_batch, merge_sample_windows, cpu_inventory_rows
from collectors.parsers import parse_perf_stat
from collectors.perf import start_stat, finish_stat, start_record, finish_record
from collectors.tests.test_collectors import FIO, METADATA, SCENARIO


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds

    def now(self):
        return (datetime(2026, 9, 29, tzinfo=timezone.utc) + timedelta(seconds=self.value)).isoformat()


class Reader:
    hz = 100

    def __init__(self, clock, reused=False):
        self.clock, self.reused = clock, reused

    def topology(self):
        return {"schedstats_enabled": "1", "network_devices": [{"name": "eth0", "numa_node": "0", "irqs": {"52": {"allowed_cpu_list": "0-1"}}}],
                "cpus": {f"cpu{cpu}": {"numa_node": 0, "core_id": "0", "socket_id": "0", "thread_siblings": "0-1"} for cpu in (0, 1)}}

    def discover(self):
        value = self.clock.value
        ticks = int(min(value, 3) * 100 + max(0, value - 3) * 20)
        born = 2000 if self.reused and value >= 6 else 1000
        item = {"pid": 100, "tid": 101, "starttime_ticks": born, "comm": "gfapi-opt", "state": "R", "process_role": "stord", "pool_role": "gfapi-opt",
                "utime_ticks": ticks, "stime_ticks": 0, "voluntary_switches": int(value), "involuntary_switches": 0,
                "runqueue_wait_ns": int(value * 1e6), "migrations": int(value >= 6), "processor": int(value >= 6), "allowed_cpus": "0-1", "allowed_mems": "0"}
        identity = f"100:101:{born}"
        item["identity"] = identity
        return {identity: item}, {"100": {"process_role": "stord", "numa_resident_pages": {"node0": 20}}}

    def host_snapshot(self):
        value = self.clock.value
        user = int(min(value, 3) * 100 + max(0, value - 3) * 20)
        total = int(value * 100)
        cpus = {"cpu0": {"user": user, "nice": 0, "system": 0, "idle": total - user, "iowait": 0, "irq": 0, "softirq": 0, "steal": 0},
                "cpu1": {"user": int(value * 10), "nice": 0, "system": 0, "idle": int(value * 90), "iowait": 0, "irq": 0, "softirq": 0, "steal": 0}}
        return {"cpu": cpus, "interrupts": {"52": {"description": "eth0", "counts": {"cpu0": int(value * 10), "cpu1": int(value * 20)}}},
                "softirqs": {}, "network": {"eth0": {"rx_packets": int(value * 100), "tx_packets": 0, "rx_dropped": int(value), "tx_dropped": 0}}}


def captured(role="guest", reused=False):
    clock = Clock()
    with tempfile.TemporaryDirectory() as directory, patch("collectors.capture.platform.system", return_value="Linux"), patch("collectors.capture.time.monotonic", clock.monotonic), patch("collectors.capture.time.sleep", clock.sleep), patch("collectors.capture.utc_now", clock.now):
        return collect("synthetic-time-run", role, Path(directory) / "bundle.json", duration=6, warmup=3, interval=3,
                       use_perf=False, record_seconds=0, proc_reader=Reader(clock, reused), scenario=deepcopy(SCENARIO))


def fio_measurement():
    payload = deepcopy(FIO)
    payload["jobs"][0]["write"].update(runtime=6000, total_ios=900, iops=150)
    return payload


class TimeSeriesTests(unittest.TestCase):
    def test_defaults_and_exact_deadlines(self):
        args = parser().parse_args(["collect", "--run-id", "r", "--host-role", "guest", "--out", "out.json"])
        self.assertEqual((args.warmup, args.duration, args.interval), (30, 60, 3))
        deadlines = sample_deadlines(30, 60, 3)
        self.assertEqual(len(deadlines), 30)
        self.assertEqual(deadlines[9], (30, "warmup"))
        self.assertEqual(deadlines[10], (33, "measurement"))
        self.assertEqual(deadlines[-1], (90, "measurement"))
        self.assertEqual(sample_deadlines(2, 5, 3), [(2, "warmup"), (5, "measurement"), (7, "measurement")])

    def test_perf_launch_failure_preserves_null_counters_and_error_evidence(self):
        with patch("collectors.perf.subprocess.Popen", side_effect=PermissionError("synthetic launch denied")), tempfile.TemporaryDirectory() as directory:
            stat = finish_stat(start_stat([101], 3, "/synthetic/perf"), 3)
            record = finish_record(start_record([101], 1, Path(directory) / "perf.data", "/synthetic/perf"))
        self.assertEqual(stat["rows"], [])
        self.assertEqual(stat["status"], "unavailable")
        self.assertIn("launch denied", stat["reason"])
        self.assertIsNone(record["path"])
        self.assertIn("launch denied", record["reason"])

    def test_real_lifecycle_keeps_windows_and_excludes_warmup_summary(self):
        bundle = captured("local")
        self.assertEqual(bundle["observation"]["warmup_s"], 3)
        self.assertEqual(bundle["measurement_duration_s"], 6)
        self.assertEqual(bundle["threads"][0]["cpu_time_ms"], 1200)
        self.assertEqual(bundle["host_deltas"]["cpu"]["cpu0"]["busy_pct"], 20)
        self.assertEqual([row["duration_s"] for row in bundle["intervals"]], [3, 3, 3])
        self.assertIsNone(bundle["threads"][0]["instructions"])

    def test_merge_keeps_individual_cpu_drift_smt_irq_and_true_io_points(self):
        bundles = [captured(role) for role in ("guest", "local", "remote")]
        counts = [{"start_offset_s": 0, "end_offset_s": 3, "completed_ios": 99999, "latency_ms": 2},
                  {"start_offset_s": 3, "end_offset_s": 6, "completed_ios": 300, "latency_ms": 1},
                  {"start_offset_s": 6, "end_offset_s": 9, "completed_ios": 600, "latency_ms": 0.5}]
        result = build_batch(bundles, METADATA, SCENARIO, fio_measurement(), fio_samples=counts)
        scenario = result["scenarios"][0]
        self.assertEqual(scenario["window"]["completed_ios"], 900)
        self.assertEqual([row["iops"] for row in scenario["sample_windows"]], [33333, 100, 200])
        self.assertEqual(scenario["sample_windows"][1]["threads"][0]["previous_cpu_id"], 0)
        self.assertEqual(scenario["sample_windows"][1]["threads"][0]["cpu_id"], 1)
        self.assertEqual(scenario["sample_windows"][1]["threads"][0]["cpu_pct"], 20)
        self.assertEqual(scenario["sample_windows"][1]["cpus"][0]["smt_sibling_cpu_ids"], [0, 1])
        self.assertEqual(scenario["sample_windows"][1]["network"][1]["irq_per_s"], 30)
        self.assertEqual(len(scenario["sample_windows"][1]["threads"]), 3)
        self.assertTrue(all(not row["layers"] for row in scenario["sample_windows"]))

    def test_missing_guest_points_and_full_run_perf_are_not_copied(self):
        bundle = captured()
        bundle["perf_stat"]["rows"] = parse_perf_stat("gfapi-opt-101;900000;;instructions;9000000000;100;;\ngfapi-opt-101;900000;;cycles;9000000000;100;;")
        result = build_batch([bundle], METADATA, SCENARIO, fio_measurement())
        self.assertTrue(all(row["iops"] is None and row["latency_ms"] is None for row in result["scenarios"][0]["sample_windows"]))
        self.assertTrue(all(thread["instructions"] is None and thread["cycles"] is None for row in result["scenarios"][0]["sample_windows"] for thread in row["threads"]))

    def test_all_observed_online_cpus_are_kept_without_thread_or_load_filter(self):
        bundle = captured("local")
        # All target threads use CPUs 0/1; CPU 7 has no target work and is idle.
        for sample in bundle["samples"]:
            sample["host"]["cpu"]["cpu7"] = {"user": 0, "nice": 0, "system": 0, "idle": int(sample["offset_s"] * 100), "iowait": 0, "irq": 0, "softirq": 0, "steal": 0}
        bundle["topology_start"]["cpus"]["cpu7"] = {"numa_node": 1, "core_id": "3", "socket_id": "1", "thread_siblings": "7"}
        _, windows = merge_sample_windows([bundle], None, .1)
        for window in windows:
            row = next(row for row in window["cpus"] if row["cpu_id"] == 7)
            self.assertEqual((row["busy_pct"], row["idle_pct"]), (0, 100))
        self.assertEqual({row["cpu_id"] for row in cpu_inventory_rows(bundle)}, {0, 1, 7})

    def test_missing_reset_or_zero_tick_cpu_stays_missing_with_inventory(self):
        bundle = captured("local")
        # Declared online CPU 9 is missing at all proc endpoints, CPU 8 has no
        # ticks, and CPU 1 disappears at one endpoint. None is synthesized idle.
        bundle["topology_start"]["online_cpu_ids"] = [0, 1, 8, 9]
        for sample in bundle["samples"]:
            sample["host"]["cpu"]["cpu8"] = {key: 0 for key in sample["host"]["cpu"]["cpu0"]}
        bundle["samples"][2]["host"]["cpu"].pop("cpu1")
        result = build_batch([bundle], METADATA, SCENARIO, fio_measurement())
        sample = result["scenarios"][0]
        self.assertEqual({row["cpu_id"] for row in sample["cpu_inventory"]}, {0, 1, 8, 9})
        self.assertEqual({row["cpu_id"] for row in sample["sample_windows"][1]["cpus"]}, {0})
        self.assertTrue(any("CPU_INCOMPLETE_WINDOWS" in note for note in sample["quality"]))
        bundle = captured("local")
        bundle["samples"][2]["host"]["cpu"]["cpu1"]["user"] = 0
        self.assertNotIn(1, {row["cpu_id"] for row in merge_sample_windows([bundle], None, .1)[1][1]["cpus"]})

    def test_per_window_counters_require_identity_and_window_alignment(self):
        bundle = captured(reused=True)
        for row in bundle["intervals"]:
            row["pmu_window_aligned"] = True
            row["perf_stat"]["rows"] = parse_perf_stat("gfapi-opt-101;600;;instructions;3000000000;100;;\ngfapi-opt-101;300;;cycles;3000000000;100;;")
            row["perf_stat"].update(started_at=row["started_at"], ended_at=row["ended_at"])
        _, windows = merge_sample_windows([bundle], None, .1)
        self.assertEqual(windows[0]["threads"][0]["instructions"], 600)
        reused_rows = windows[1]["threads"]
        self.assertEqual(len(reused_rows), 2)
        self.assertTrue(all(row["instructions"] is None for row in reused_rows))
        self.assertIsNone(next(row for row in reused_rows if row["state"] == "exited")["cpu_id"])
        self.assertIsNone(next(row for row in reused_rows if row["starttime_ticks"] == 2000)["previous_cpu_id"])
        self.assertEqual(windows[2]["threads"][0]["instructions"], 600)
        bundle["intervals"][2]["pmu_window_aligned"] = False
        self.assertIsNone(merge_sample_windows([bundle], None, .1)[1][2]["threads"][0]["instructions"])

    def test_short_thread_enabled_time_is_not_a_wallclock_coverage_failure(self):
        bundle = captured()
        row = bundle["intervals"][1]
        row["pmu_window_aligned"] = True
        row["perf_stat"].update(started_at=row["started_at"], ended_at=row["ended_at"],
                                rows=parse_perf_stat("gfapi-opt-101;600;;instructions;100000000;100;;\ngfapi-opt-101;300;;cycles;100000000;100;;"))
        thread = merge_sample_windows([bundle], None, .1)[1][1]["threads"][0]
        self.assertEqual(thread["cpu_pct"], 20)
        self.assertEqual(thread["counting_ratio"], 1)
        self.assertEqual(thread["instructions"] / thread["cycles"], 2)
        row["perf_stat"]["ended_at"] = row["started_at"]
        self.assertIsNone(merge_sample_windows([bundle], None, .1)[1][1]["threads"][0]["instructions"])

    def test_host_or_guest_window_mismatch_and_warmup_summary_rejected(self):
        guest, local = captured(), captured("local")
        local["intervals"][1]["start_offset_s"] += .5
        with self.assertRaisesRegex(ValueError, "sample windows differ"):
            merge_sample_windows([guest, local], None, .1)
        with self.assertRaisesRegex(ValueError, "unique collector window"):
            merge_sample_windows([guest], [{"start_offset_s": 2, "end_offset_s": 6, "completed_ios": 1}], .1)
        with self.assertRaisesRegex(ValueError, "measurement duration only"):
            build_batch([guest], METADATA, SCENARIO, FIO)


if __name__ == "__main__":
    unittest.main()
