"""Local collector lifecycle; observes an independently scheduled fio workload."""

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import socket
from threading import Thread
import time

from .parsers import counter_delta, cpu_delta, interrupt_delta, parse_fio_json
from .perf import finish_record, finish_stat, perf_binary, start_record, start_stat
from .proc import ProcReader


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    temp.replace(path)


def thread_delta(before, after, hz, seconds, schedstats_enabled):
    result = []
    for identity in sorted(set(before) & set(after)):
        initial, final = before[identity], after[identity]
        ticks = counter_delta(initial["utime_ticks"] + initial["stime_ticks"], final["utime_ticks"] + final["stime_ticks"])
        voluntary = counter_delta(initial["voluntary_switches"], final["voluntary_switches"])
        involuntary = counter_delta(initial["involuntary_switches"], final["involuntary_switches"])
        result.append({"identity": identity, "tid": final["tid"], "pid": final["pid"],
                       "process_role": final["process_role"], "pool_role": final["pool_role"],
                       "cpu_time_ms": ticks * 1000 / hz if ticks is not None else None,
                       "cpu_pct": ticks * 100 / hz / seconds if ticks is not None and seconds > 0 else None,
                       "runqueue_wait_ms": counter_delta(initial["runqueue_wait_ns"], final["runqueue_wait_ns"]) / 1000000 if schedstats_enabled and counter_delta(initial["runqueue_wait_ns"], final["runqueue_wait_ns"]) is not None else None,
                       "context_switches": voluntary + involuntary if voluntary is not None and involuntary is not None else None,
                       "migrations": counter_delta(initial["migrations"], final["migrations"]),
                       "cpu_ids": sorted({initial["processor"], final["processor"]}),
                       "allowed_cpu_list": final["allowed_cpus"], "allowed_mem_list": final["allowed_mems"]})
    return result


def _sum_complete(values):
    return sum(values) if values and all(value is not None for value in values) else None


def cpu_list(text):
    values = set()
    for segment in (text or "").split(","):
        if not segment:
            continue
        bounds = segment.split("-")
        try:
            start, end = int(bounds[0]), int(bounds[-1])
            if 0 <= start <= end <= 1048576:
                values.update(range(start, end + 1))
        except ValueError:
            continue
    return sorted(values)


def thread_branch(host_role, process_role, pool):
    return "guest" if host_role == "guest" else f"{host_role}-gluster-unclassified" if process_role == "glusterfsd" else f"{host_role}-data" if pool.startswith("tierd") or process_role == "glusterfsd-data" else "protocol" if process_role == "qemu" or pool == "nfs-rpc" else "business"


def summarize_threads(bundle):
    groups = defaultdict(list)
    intervals = [item for item in bundle["intervals"] if item.get("phase", "measurement") == "measurement"]
    start_index = next((index for index, item in enumerate(bundle["intervals"]) if item.get("phase", "measurement") == "measurement"), 0)
    snapshots = bundle["samples"][start_index:]
    all_groups = set()
    for sample in snapshots:
        for item in sample["threads"].values():
            all_groups.add((item["process_role"], item["pool_role"]))
    for interval in intervals:
        for item in interval["threads"]:
            groups[(item["process_role"], item["pool_role"])].append(item)
    topology = bundle["topology_start"]["cpus"]
    results = []
    for role, pool in sorted(all_groups):
        observed = {identity: item for sample in snapshots for identity, item in sample["threads"].items() if (item["process_role"], item["pool_role"]) == (role, pool)}
        rows = groups[(role, pool)]
        stable = all(set(identity for identity, item in sample["threads"].items() if (item["process_role"], item["pool_role"]) == (role, pool)) == set(observed) for sample in snapshots)
        cpu_ids = sorted({cpu for row in rows for cpu in row["cpu_ids"]})
        numa_nodes = sorted({topology.get(f"cpu{cpu}", {}).get("numa_node") for cpu in cpu_ids if topology.get(f"cpu{cpu}", {}).get("numa_node") is not None})
        memory_nodes = sorted({int(node[4:]) for sample in snapshots for pid, process in sample["processes"].items()
                               if process["process_role"] == role for node, pages in process["numa_resident_pages"].items() if pages > 0})
        pmu_rows = []
        complete_pmu = bool(intervals) and stable
        interval_pmu = any("perf_stat" in interval for interval in intervals)
        sources = [interval.get("perf_stat", {}).get("rows", []) for interval in intervals] if interval_pmu else [bundle["perf_stat"].get("rows", [])]
        for tid in {item["tid"] for item in observed.values()}:
            matches = [row for source in sources for row in source if row["tid"] == tid]
            events = {"instructions" if "instructions" in row["event"] else "cycles" for row in matches}
            if events != {"instructions", "cycles"} or any(row["estimated_count"] is None for row in matches):
                complete_pmu = False
            if interval_pmu and any(not interval.get("pmu_window_aligned", False) or len([row for row in source if row["tid"] == tid and "instructions" in row["event"]]) != 1 or len([row for row in source if row["tid"] == tid and "cycles" in row["event"] and "instructions" not in row["event"]]) != 1 for interval, source in zip(intervals, sources)):
                complete_pmu = False
            pmu_rows.extend(matches)
        instructions = _sum_complete([row["estimated_count"] for row in pmu_rows if "instructions" in row["event"]]) if complete_pmu else None
        cycles = _sum_complete([row["estimated_count"] for row in pmu_rows if "cycles" in row["event"] and "instructions" not in row["event"]]) if complete_pmu else None
        # Scaling yields estimates, not fractional retired instructions. Preserve
        # raw/per-event floats in artifacts; normalize estimated totals to counts.
        instructions = round(instructions) if instructions is not None else None
        cycles = round(cycles) if cycles is not None else None
        ratio_values = [row["running_pct"] / 100 for row in pmu_rows if row["running_pct"] is not None]
        results.append({"host_role": bundle["host_role"], "process_role": role, "pool": pool,
                        "branch": thread_branch(bundle["host_role"], role, pool),
                        "thread_count": max(sum((item["process_role"], item["pool_role"]) == (role, pool) for item in sample["threads"].values()) for sample in snapshots),
                        "cpu_time_ms": _sum_complete([row["cpu_time_ms"] for row in rows]) if stable else None,
                        "instructions": instructions, "cycles": cycles,
                        "runqueue_wait_ms": _sum_complete([row["runqueue_wait_ms"] for row in rows]) if stable else None,
                        "context_switches": _sum_complete([row["context_switches"] for row in rows]) if stable else None,
                        "migrations": _sum_complete([row["migrations"] for row in rows]) if stable else None,
                        "cpu_ids": cpu_ids,
                        "allowed_cpu_ids": sorted({cpu for item in observed.values() for cpu in cpu_list(item["allowed_cpus"])}),
                        "numa_nodes": numa_nodes, "memory_numa_nodes": memory_nodes,
                        "remote_access_pct": None, "counting_ratio": min(ratio_values) if ratio_values and complete_pmu else None,
                        "counter_scope": "measurement-only sum of per-window user+kernel grouped instructions/cycles; perf scaled estimates" if complete_pmu and interval_pmu else "user+kernel grouped instructions/cycles; perf scaled estimates" if complete_pmu else "unavailable",
                        "top_functions": []})
    return results


def host_delta(before, after):
    return {"cpu": cpu_delta(before["cpu"], after["cpu"]),
            "interrupts": interrupt_delta(before["interrupts"], after["interrupts"]),
            "softirqs": interrupt_delta(before["softirqs"], after["softirqs"]),
            "network": {name: {key: counter_delta(before["network"].get(name, {}).get(key), value) for key, value in counters.items()} for name, counters in after["network"].items()}}


def sample_deadlines(warmup, duration, interval):
    """Split at the phase boundary even when it is not a polling multiple."""
    result = []
    for phase, start, length in (("warmup", 0, warmup), ("measurement", warmup, duration)):
        elapsed = 0.0
        while elapsed < length:
            elapsed = min(length, elapsed + interval)
            result.append((start + elapsed, phase))
    return result


def collect(run_id, host_role, outfile, *, duration=60, warmup=0, interval=3, fio_json=None, roles=None, use_perf=True, record_seconds=5, proc_reader=None, scenario=None, start_at=None):
    if platform.system() != "Linux":
        raise RuntimeError("Live collection requires Linux /proc and perf; Windows can run parsers, tests, and merge only.")
    if not run_id.strip() or host_role not in ("guest", "local", "remote"):
        raise ValueError("run_id and guest/local/remote host_role are required")
    if not 0.1 <= interval <= duration <= 86400 or not 0 <= warmup <= 86400 or not 0 <= record_seconds <= duration:
        raise ValueError("Require 0.1 <= interval <= measurement duration <= 86400, 0 <= warmup <= 86400, and 0 <= record_seconds <= duration")
    outfile = Path(outfile)
    if outfile.exists():
        raise FileExistsError("Output exists; use a new filename to preserve prior evidence")
    reader = proc_reader or ProcReader(host_role, roles=roles)
    binary = perf_binary() if use_perf else None
    artifact_dir = outfile.parent / (outfile.stem + ".artifacts")
    artifact_dir.mkdir(parents=True, exist_ok=False)
    bundle = {"collector_schema": 1, "collector_version": "0.2.0", "run_id": run_id, "host_role": host_role,
              "host_id": socket.gethostname(), "source": "measured", "architecture": platform.machine(),
              "kernel": platform.release(), "duration_requested_s": duration, "warmup_requested_s": warmup, "interval_requested_s": interval,
              "started_at": utc_now(), "topology_start": reader.topology(), "samples": [], "intervals": [], "quality": [], "artifacts": []}
    if scenario is not None:
        bundle["scenario"] = scenario
    if start_at:
        scheduled = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        if scheduled.tzinfo is None:
            raise ValueError("--start-at requires a timezone")
        while (remaining := (scheduled - datetime.now(timezone.utc)).total_seconds()) > 0:
            time.sleep(min(remaining, 1))
    initial_threads, initial_processes = reader.discover()
    first_host = reader.host_snapshot()
    bundle["started_at"] = utc_now()
    start = time.monotonic()
    bundle["samples"].append({"offset_s": 0, "timestamp": bundle["started_at"], "threads": initial_threads, "processes": initial_processes, "host": first_host})
    record_handle = None
    if not initial_threads:
        bundle["quality"].append("No matching target process/thread discovered; process roles must be verified or overridden.")
    if not binary:
        bundle["quality"].append("perf unavailable or disabled; hardware counters and stacks remain missing.")
    schedstats = bundle["topology_start"]["schedstats_enabled"] == "1"
    if not schedstats:
        bundle["quality"].append("schedstats not enabled/observable; runnable wait is unknown, not zero.")
    warmup_actual = 0.0
    for deadline, phase in sample_deadlines(warmup, duration, interval):
        previous = bundle["samples"][-1]
        seconds = deadline - (time.monotonic() - start)
        if seconds <= 0:
            raise RuntimeError("Collector missed a sampling deadline; reduce collection overhead or increase interval")
        if phase == "measurement" and record_handle is None and record_seconds > 0:
            record_handle = start_record([item["tid"] for item in previous["threads"].values()], record_seconds, artifact_dir / "perf.data", binary) if binary else None
        # Reattach each interval to the currently discovered TIDs. A later-born
        # thread gets counters next interval; no whole-run counter is copied.
        handle = start_stat([item["tid"] for item in previous["threads"].values()], max(0.01, seconds - 0.02), binary) if binary else None
        stat_result = {}
        def wait_for_stat(current_handle=handle, requested_seconds=seconds, target=stat_result):
            target.update(finish_stat(current_handle, requested_seconds))
        worker = Thread(target=wait_for_stat, daemon=True)
        worker.start()
        while (remaining := deadline - (time.monotonic() - start)) > 0:
            time.sleep(min(remaining, 1))
        threads, processes = reader.discover()
        host = reader.host_snapshot()
        elapsed = time.monotonic() - start
        sample = {"offset_s": elapsed, "timestamp": utc_now(), "threads": threads, "processes": processes, "host": host}
        deltas = thread_delta(previous["threads"], threads, reader.hz, elapsed - previous["offset_s"], schedstats)
        worker.join(timeout=6)
        stat_result = stat_result or {"status": "unavailable", "reason": "perf worker did not finish", "rows": []}
        aligned = False
        if stat_result.get("started_at") and stat_result.get("ended_at"):
            aligned = abs((datetime.fromisoformat(stat_result["started_at"]) - datetime.fromisoformat(previous["timestamp"])).total_seconds()) <= 0.1 and abs((datetime.fromisoformat(stat_result["ended_at"]) - datetime.fromisoformat(sample["timestamp"])).total_seconds()) <= 0.1
        bundle["intervals"].append({"started_at": previous["timestamp"], "ended_at": sample["timestamp"],
                                    "start_offset_s": previous["offset_s"], "end_offset_s": elapsed, "phase": phase,
                                    "duration_s": elapsed - previous["offset_s"], "threads": deltas,
                                    "perf_stat": stat_result, "pmu_window_aligned": aligned,
                                    "host_deltas": host_delta(previous["host"], host),
                                    "new_threads": sorted(set(threads) - set(previous["threads"])),
                                    "exited_threads": sorted(set(previous["threads"]) - set(threads))})
        bundle["samples"].append(sample)
        if phase == "warmup":
            warmup_actual = elapsed
    bundle["ended_at"] = bundle["samples"][-1]["timestamp"]
    bundle["duration_s"] = bundle["samples"][-1]["offset_s"]
    measurement = [item for item in bundle["intervals"] if item["phase"] == "measurement"]
    bundle["observation"] = {"started_at": bundle["started_at"], "warmup_s": warmup_actual,
                              "measurement_s": bundle["duration_s"] - warmup_actual, "interval_s": interval}
    bundle["measurement_started_at"] = measurement[0]["started_at"]
    bundle["measurement_ended_at"] = measurement[-1]["ended_at"]
    bundle["measurement_duration_s"] = sum(item["duration_s"] for item in measurement)
    bundle["perf_stat"] = {"status": "ok" if all(item["perf_stat"]["status"] == "ok" and item["pmu_window_aligned"] for item in measurement) else "partial",
                           "started_at": bundle["measurement_started_at"], "ended_at": bundle["measurement_ended_at"],
                           "rows": [row for item in measurement for row in item["perf_stat"].get("rows", [])],
                           "scope": "measurement-only per-window grouped counters; raw intervals retain timing/errors"}
    stat_path = artifact_dir / "perf-stat.json"
    write_json(stat_path, bundle["perf_stat"])
    bundle["artifacts"].append({"kind": "perf_stat", "path": str(stat_path.resolve()),
                                "note": "Measurement-only concatenated per-window perf counters; each interval reattaches current TIDs. Raw bundle retains actual counter windows."})
    if bundle["perf_stat"]["status"] != "ok":
        bundle["quality"].append("perf counters incomplete; permission/PMU/target errors retained in raw artifact.")
    bundle["topology_end"] = reader.topology()
    record = finish_record(record_handle)
    bundle["perf_record"] = record
    write_json(artifact_dir / "perf-record-status.json", record)
    if record.get("path"):
        bundle["artifacts"].append({"kind": "perf_record", "path": str(Path(record["path"]).resolve()), "note": record["note"]})
    bundle["quality"].extend([
        "Pool identity uses host/process-role/thread-pool, not PID/TID; PID/TID/starttime are raw lifecycle IDs.",
        "glusterfsd DATA vs ARBITER and volume identity are not inferred from process name; verify role mapping.",
        "NUMA page placement is process-wide, not measured remote-access rate; remote_access_pct is unknown.",
        "perf record attaches measurement-start TIDs only; top-function percentages are not inferred from the recording file.",
        "Product soft-affinity, off-CPU blocking stack traces, and competing tasks are not collected by this starter.",
        "Clock synchronization and guest completed-IO alignment require orchestration metadata at merge time.",
        "proc snapshots are sequential reads; observed CPU IDs are sampled endpoints, not a complete CPU residency trace.",
        "Periodic /proc discovery can miss threads born and exited between polls; observed stable pools do not certify absence of transient work.",
        "Per-window perf starts/stops have overhead and can perturb the workload. PMU windows outside 0.1s endpoint alignment are invalidated; running coverage is retained.",
        "Observation offsets use actual proc sample endpoints, including polling drift. Requested 30s/60s/3s cadence and actual phase durations are separately recorded.",
        "Individual thread CPU rates above 100% from proc tick quantization/read skew are withheld as unknown; raw ticks and pool summaries remain in evidence.",
    ])
    if any(item["new_threads"] or item["exited_threads"] for item in bundle["intervals"]):
        bundle["quality"].append("Thread lifecycle changed during capture; affected pool totals are incomplete and null.")
    start_index = next(index for index, item in enumerate(bundle["intervals"]) if item["phase"] == "measurement")
    first, last = bundle["samples"][start_index]["host"], bundle["samples"][-1]["host"]
    bundle["host_deltas"] = host_delta(first, last)
    bundle["threads"] = summarize_threads(bundle)
    if fio_json:
        path = Path(fio_json)
        raw = path.read_bytes()
        bundle["fio_summary"] = parse_fio_json(json.loads(raw))
        bundle["artifacts"].append({"kind": "fio_json", "path": str(path.resolve()), "note": "Existing fio result only; collector does not run fio. SHA256=" + hashlib.sha256(raw).hexdigest()})
    bundle["quality"] = list(dict.fromkeys(bundle["quality"]))
    write_json(outfile, bundle)
    return bundle
