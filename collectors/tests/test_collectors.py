"""Synthetic /proc/perf/fio evidence only; never contacts or profiles a cluster."""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collectors.capture import collect, summarize_threads, thread_delta
from collectors.merge import build_batch
from collectors.parsers import counter_delta, cpu_delta, parse_cpu_stat, parse_fio_json, parse_interrupts, parse_perf_stat, parse_task_stat
from collectors.proc import ProcReader, parse_cpu_list


def stat(tid, comm, utime=10, stime=5, born=1000, cpu=2):
    values = ["0"] * 40
    values[0], values[11], values[12], values[19], values[36] = "S", str(utime), str(stime), str(born), str(cpu)
    return f"{tid} ({comm}) " + " ".join(values)


FIO = {"jobs": [{"jobname": "fio0", "job options": {"rw": "randwrite", "bs": "4k", "iodepth": "64", "numjobs": "8", "ioengine": "libaio", "direct": "1"},
                 "write": {"total_ios": 1000, "iops": 100, "bw_bytes": 409600, "lat_ns": {"mean": 1000000}, "runtime": 10000}}]}
SCENARIO = {"id": "nfs-write", "title": "4K 写", "protocol": "nfs", "fio": {"rw": "randwrite", "bs": "4k", "iodepth": 64, "numjobs": 8, "vm_count": 1, "ioengine": "libaio", "direct": 1}}
METADATA = {"id": "fullfio-1", "title": "测试导入", "build": "build-1", "environment": {"id": "env-1", "label": "合成测试环境", "description": "Unit-test fixture only"}, "changes": []}
WINDOW = {"started_at": "2026-09-29T01:00:00+00:00", "ended_at": "2026-09-29T01:00:10+00:00", "completed_ios": 1000}


def bundle(role):
    return {"collector_schema": 1, "run_id": "fio-one", "host_role": role, "source": "measured", "duration_s": 10,
            "started_at": WINDOW["started_at"], "ended_at": WINDOW["ended_at"], "scenario": deepcopy(SCENARIO),
            "quality": ["Synthetic unit-test fixture"], "threads": [], "artifacts": [],
            "perf_stat": {"status": "unavailable"},
            "topology_start": {"cpus": {"cpu0": {"numa_node": None}}, "network_devices": []},
            "host_deltas": {"cpu": {"cpu0": {"total_ticks": 100, "delta_ticks": {"user": 10, "nice": 0, "system": 10, "irq": 5, "softirq": 5, "idle": 65, "iowait": 5, "steal": 0}}}, "interrupts": {}, "network": {}}}


class ParserTests(unittest.TestCase):
    def test_proc_stat_with_parentheses_and_counter_reset(self):
        result = parse_task_stat(stat(42, "a (thread) worker", 10, 20, 33, 7))
        self.assertEqual(result["comm"], "a (thread) worker")
        self.assertEqual((result["utime_ticks"], result["stime_ticks"], result["starttime_ticks"], result["processor"]), (10, 20, 33, 7))
        self.assertIsNone(counter_delta(100, 50))
        self.assertIsNone(counter_delta(None, 50))

    def test_cpu_guest_not_counted_twice_and_irq_mapping(self):
        before = parse_cpu_stat("cpu0 10 0 10 70 5 2 3 0 10 0\n")
        after = parse_cpu_stat("cpu0 20 0 20 140 10 4 6 0 20 0\n")
        result = cpu_delta(before, after)["cpu0"]
        self.assertEqual(result["total_ticks"], 100)
        self.assertEqual(result["busy_pct"], 25)
        irqs = parse_interrupts(" CPU0 CPU1\n 52: 10 20 PCI-MSI eth0-rx-0\n ERR: 1\n")
        self.assertEqual(irqs["52"]["counts"], {"cpu0": 10, "cpu1": 20})
        self.assertNotIn("ERR", irqs)

    def test_perf_scaled_counts_missing_not_zero(self):
        rows = parse_perf_stat("tierd-core-123;1000;;instructions;500000000;50.00;;\ntierd-core-123;2000;;cycles;500000000;50.00;;\nx-124;<not supported>;;instructions;0;0;;\n")
        self.assertEqual(rows[0]["tid"], 123)
        self.assertEqual(rows[0]["estimated_count"], 2000)
        self.assertEqual(rows[0]["time_enabled_ns"], 1000000000)
        self.assertIsNone(rows[2]["raw_count"])
        self.assertIsNone(rows[2]["estimated_count"])

    def test_fio_weighted_total_latency_and_missing_counter(self):
        sample = deepcopy(FIO)
        sample["jobs"][0]["read"] = {"total_ios": 100, "iops": 10, "bw": 40, "lat_us": {"mean": 2000}}
        result = parse_fio_json(sample)
        self.assertEqual(result["completed_ios"], 1100)
        self.assertEqual(result["iops"], 110)
        self.assertAlmostEqual(result["latency_ms"], 1200 / 1100)
        sample["jobs"][0]["write"].pop("lat_ns")
        self.assertIsNone(parse_fio_json(sample)["latency_ms"])

    def test_synthetic_proc_discovers_quiet_threads_and_numa(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            process = root / "proc/123"
            task = process / "task/124"
            task.mkdir(parents=True)
            (process / "comm").write_text("asan-stord")
            (process / "numa_maps").write_text("123 default N0=20 N1=10\n")
            (process / "cgroup").write_text("0::/storage")
            (task / "stat").write_text(stat(124, "tierd-core", 0, 0))
            (task / "status").write_text("Cpus_allowed_list:\t2-3\nMems_allowed_list:\t0-1\nvoluntary_ctxt_switches:\t0\nnonvoluntary_ctxt_switches:\t0\n")
            (task / "schedstat").write_text("0 0 0")
            (task / "sched").write_text("se.nr_migrations : 2")
            reader = ProcReader("local", proc_root=root / "proc", sys_root=root / "sys")
            threads, processes = reader.discover()
            self.assertEqual(len(threads), 1)
            self.assertEqual(next(iter(threads.values()))["pool_role"], "tierd-core")
            self.assertEqual(processes["123"]["numa_resident_pages"], {"node0": 20, "node1": 10})
            self.assertEqual(next(iter(threads.values()))["migrations"], 2)

    def test_online_cpu_list_is_explicit_and_missing_does_not_mean_zero(self):
        self.assertEqual(parse_cpu_list("0-2,7,64-66"), [0, 1, 2, 7, 64, 65, 66])
        self.assertIsNone(parse_cpu_list(None))
        self.assertIsNone(parse_cpu_list("2-0"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / "sys/devices/system/cpu"
            directory.mkdir(parents=True)
            (directory / "online").write_text("0-1")
            (directory / "cpu7").mkdir()  # Existing sys directory need not be online.
            topology = ProcReader("local", proc_root=root / "proc", sys_root=root / "sys").topology()
            self.assertEqual(topology["online_cpu_ids"], [0, 1])
            self.assertIn("cpu7", topology["cpus"])

    def test_windows_live_collection_rejected_before_output(self):
        with tempfile.TemporaryDirectory() as temporary, patch("collectors.capture.platform.system", return_value="Windows"):
            output = Path(temporary) / "out.json"
            with self.assertRaisesRegex(RuntimeError, "requires Linux"):
                collect("run", "guest", output)
            self.assertFalse(output.exists())

    def test_pool_sums_counters_and_lifecycle_gap_is_not_fabricated(self):
        initial = {}
        final = {}
        for tid, ticks in ((1, 10), (2, 20)):
            item = {"identity": str(tid), "tid": tid, "pid": 100, "process_role": "stord", "pool_role": "tierd-core",
                    "utime_ticks": 0, "stime_ticks": 0, "voluntary_switches": 0, "involuntary_switches": 0,
                    "migrations": 0, "runqueue_wait_ns": 0, "processor": 0, "allowed_cpus": "0-1", "allowed_mems": "0"}
            initial[str(tid)] = item
            final[str(tid)] = dict(item, utime_ticks=ticks)
        rows = parse_perf_stat("a-1;100;;instructions;1000000000;100;;\na-1;100;;cycles;1000000000;100;;\na-2;900;;instructions;1000000000;100;;\na-2;300;;cycles;1000000000;100;;\n")
        sample = {"threads": initial, "processes": {"100": {"process_role": "stord", "numa_resident_pages": {"node0": 20}}}}
        profile = {"host_role": "local", "samples": [sample, dict(sample, threads=final)],
                   "intervals": [{"threads": thread_delta(initial, final, 100, 1, False)}],
                   "topology_start": {"cpus": {"cpu0": {"numa_node": 0}}}, "perf_stat": {"rows": rows}}
        result = summarize_threads(profile)[0]
        self.assertEqual(result["cpu_time_ms"], 300)
        self.assertEqual(result["instructions"], 1000)
        self.assertEqual(result["cycles"], 400)  # Ratio=2.5, not mean(1,3)=2.
        self.assertIsNone(result["runqueue_wait_ms"])
        self.assertEqual(result["counting_ratio"], 1)
        profile["samples"][-1]["threads"] = {"1": final["1"]}
        incomplete = summarize_threads(profile)[0]
        self.assertIsNone(incomplete["cpu_time_ms"])
        self.assertIsNone(incomplete["instructions"])
        self.assertEqual(incomplete["thread_count"], 2)


class MergeTests(unittest.TestCase):
    def test_partial_import_never_uses_whole_fio_io_denominator(self):
        result = build_batch([bundle("guest"), bundle("local"), bundle("remote")], METADATA, SCENARIO, FIO)
        scenario = result["scenarios"][0]
        self.assertEqual(result["source"], "measured")
        self.assertIsNone(scenario["window"]["completed_ios"])
        self.assertIsNone(scenario["cpus"][0]["numa_node"])
        self.assertTrue(any("MISSING_WINDOW_DENOMINATOR" in message for message in scenario["quality"]))

    def test_explicit_window_aligns_and_two_completed_batches(self):
        result = build_batch([bundle("guest"), bundle("local"), bundle("remote")], METADATA, SCENARIO, FIO, fio_window=WINDOW)
        self.assertEqual(result["scenarios"][0]["window"]["completed_ios"], 1000)
        metadata = deepcopy(METADATA)
        metadata.update(id="fullfio-2", build="build-2")
        second = build_batch([bundle("guest"), bundle("local"), bundle("remote")], metadata, SCENARIO, FIO, fio_window=WINDOW)
        self.assertNotEqual(result["id"], second["id"])
        self.assertEqual(result["environment"], second["environment"])
        self.assertEqual(result["scenarios"][0]["fio"], second["scenarios"][0]["fio"])

    def test_run_model_and_capture_window_mismatch_rejected(self):
        remote = bundle("remote")
        remote["run_id"] = "other-run"
        with self.assertRaisesRegex(ValueError, "run IDs"):
            build_batch([bundle("guest"), remote], METADATA, SCENARIO, FIO)
        remote = bundle("remote")
        remote["started_at"] = "2026-09-29T01:00:02+00:00"
        remote["ended_at"] = "2026-09-29T01:00:12+00:00"
        with self.assertRaisesRegex(ValueError, "windows differ"):
            build_batch([bundle("guest"), remote], METADATA, SCENARIO, FIO)
        bad_fio = deepcopy(FIO)
        bad_fio["jobs"][0]["job options"]["iodepth"] = "1"
        with self.assertRaisesRegex(ValueError, "iodepth"):
            build_batch([bundle("guest")], METADATA, SCENARIO, bad_fio)

    def test_window_metadata_inconsistent_and_short_pmu_disables_cost(self):
        bad_window = deepcopy(WINDOW)
        bad_window["completed_ios"] = True
        with self.assertRaisesRegex(ValueError, "non-negative integer"):
            build_batch([bundle("guest")], METADATA, SCENARIO, FIO, fio_window=bad_window)
        local = bundle("local")
        local["perf_stat"] = {"status": "ok", "started_at": WINDOW["started_at"], "ended_at": "2026-09-29T01:00:05+00:00"}
        result = build_batch([bundle("guest"), local], METADATA, SCENARIO, FIO, fio_window=WINDOW)
        self.assertIsNone(result["scenarios"][0]["window"]["completed_ios"])
        self.assertTrue(any("PMU counter window" in message for message in result["scenarios"][0]["quality"]))

    def test_partial_pmu_window_with_available_counters_disables_cost(self):
        local = bundle("local")
        local["perf_stat"] = {"status": "partial", "started_at": WINDOW["started_at"],
                              "ended_at": "2026-09-29T01:00:05+00:00", "rows": [{"raw_count": 1000}]}
        result = build_batch([bundle("guest"), local], METADATA, SCENARIO, FIO, fio_window=WINDOW)
        self.assertIsNone(result["scenarios"][0]["window"]["completed_ios"])


if __name__ == "__main__":
    unittest.main()
