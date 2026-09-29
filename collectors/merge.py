"""Normalize local observation bundles into the version Batch import contract."""

from datetime import datetime, timedelta, timezone
import json
import math
from pathlib import Path

from .capture import cpu_list, host_delta, thread_branch
from .parsers import parse_fio_json


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Window timestamps require a timezone")
    return parsed.astimezone(timezone.utc)


def _window(bundle):
    start, end = timestamp(bundle["started_at"]), timestamp(bundle["ended_at"])
    if end <= start:
        raise ValueError("Capture ended_at must follow started_at")
    if abs((end - start).total_seconds() - bundle["duration_s"]) > 0.1:
        raise ValueError("Capture duration conflicts with timestamp window")
    return start, end


def _model_value(key, value):
    if key == "bs":
        text = str(value).lower().replace("ib", "").replace("b", "")
        multipliers = {"k": 1024, "m": 1048576, "g": 1073741824}
        return int(float(text[:-1]) * multipliers[text[-1]]) if text[-1:] in multipliers else int(text)
    if key in ("iodepth", "numjobs", "direct", "vm_count"):
        return int(value)
    return str(value).lower()


def validate_fio_model(summary, model):
    quality = []
    global_options = summary.get("global_options", {})
    for key in ("rw", "bs", "iodepth", "numjobs", "ioengine", "direct"):
        observed = [options[key] for options in summary["job_options"] if key in options]
        if key in global_options:
            observed.append(global_options[key])
        if not observed:
            quality.append(f"fio option {key} absent in JSON; supplied scenario model could not be independently verified.")
        elif any(_model_value(key, value) != _model_value(key, model[key]) for value in observed):
            raise ValueError(f"fio JSON and scenario model disagree on {key}")
    if any(str(options.get("stonewall", "0")) not in ("0", "False", "false") for options in summary["job_options"]):
        raise ValueError("Sequential/stonewall fio jobs cannot be merged as simultaneous summed IOPS")
    return quality


def cpu_rows(bundle):
    rows = []
    for name, item in bundle["host_deltas"]["cpu"].items():
        total = item["total_ticks"]
        if not total or any(value is None for value in item["delta_ticks"].values()):
            continue
        ticks = item["delta_ticks"]
        percent = lambda key: round(100 * ticks[key] / total, 4)
        topology = bundle["topology_start"]["cpus"].get(name, {})
        integer = lambda value: int(value) if value is not None and str(value).isdecimal() else None
        rows.append({"host_role": bundle["host_role"], "cpu_id": int(name[3:]),
                     "numa_node": topology.get("numa_node"),
                     "user_pct": round(percent("user") + percent("nice"), 4), "system_pct": percent("system"),
                     "irq_pct": percent("irq"), "softirq_pct": percent("softirq"), "idle_pct": percent("idle"),
                     "competitors": [], "busy_pct": item.get("busy_pct"),
                     "smt_sibling_cpu_ids": cpu_list(topology.get("thread_siblings")),
                     "core_id": integer(topology.get("core_id")), "socket_id": integer(topology.get("socket_id"))})
    return rows


def cpu_inventory_rows(bundle):
    """Known online/observed logical CPUs, independent of target threads/load.

    /sys CPU directories alone can include offline CPUs. Use explicit online
    lists plus all /proc/stat endpoint IDs; missing counters stay missing rows.
    """
    samples = bundle.get("samples", [])
    observed = {int(name[3:]) for sample in samples for name in sample.get("host", {}).get("cpu", {})}
    observed.update(int(name[3:]) for name in bundle.get("host_deltas", {}).get("cpu", {}))
    for topology in (bundle.get("topology_start", {}), bundle.get("topology_end", {})):
        observed.update(topology.get("online_cpu_ids") or [])
    integer = lambda value: int(value) if value is not None and str(value).isdecimal() else None
    rows = []
    for cpu_id in sorted(observed):
        topology = bundle.get("topology_start", {}).get("cpus", {}).get(f"cpu{cpu_id}") or bundle.get("topology_end", {}).get("cpus", {}).get(f"cpu{cpu_id}", {})
        rows.append({"host_role": bundle["host_role"], "cpu_id": cpu_id,
                     "numa_node": topology.get("numa_node"), "socket_id": integer(topology.get("socket_id")),
                     "core_id": integer(topology.get("core_id")), "smt_sibling_cpu_ids": cpu_list(topology.get("thread_siblings"))})
    return rows


def network_rows(bundle):
    rows = []
    duration = bundle["duration_s"]
    devices = {device["name"]: device for device in bundle["topology_start"]["network_devices"]}
    for name, values in bundle["host_deltas"].get("network", {}).items():
        device = devices.get(name, {})
        node = device.get("numa_node")
        node = int(node) if node is not None and int(node) >= 0 else None
        packet_values = [values.get(key) for key in ("rx_packets", "tx_packets")]
        drop_values = [values.get(key) for key in ("rx_dropped", "tx_dropped")]
        rows.append({"host_role": bundle["host_role"], "interface": name, "queue": "all", "irq_id": None,
                     "cpu_ids": [], "allowed_cpu_ids": [], "numa_node": node, "irq_per_s": None,
                     "packets_per_s": sum(packet_values) / duration if all(value is not None for value in packet_values) else None,
                     "drops": sum(drop_values) if all(value is not None for value in drop_values) else None, "retransmits": None})
        for irq, affinity in device.get("irqs", {}).items():
            counts = bundle["host_deltas"]["interrupts"].get(irq, {}).get("delta_count_by_cpu", {})
            complete = bool(counts) and all(value is not None for value in counts.values())
            rows.append({"host_role": bundle["host_role"], "interface": name, "queue": f"irq:{irq}", "irq_id": int(irq),
                         "cpu_ids": sorted(int(cpu[3:]) for cpu, count in counts.items() if count is not None and count > 0),
                         "allowed_cpu_ids": cpu_list(affinity.get("allowed_cpu_list")), "numa_node": node,
                         "irq_per_s": sum(counts.values()) / duration if complete else None,
                         "packets_per_s": None, "drops": None, "retransmits": None})
    return rows


def pmu_aligned(interval):
    stat = interval.get("perf_stat", {})
    if not interval.get("pmu_window_aligned") or not stat.get("started_at") or not stat.get("ended_at"):
        return False
    return all(abs((timestamp(stat[key]) - timestamp(interval[key])).total_seconds()) <= .1 for key in ("started_at", "ended_at"))


def individual_threads(bundle, before, after, interval):
    """Endpoint placement and same-identity interval deltas, never pool copies."""
    deltas = {row["identity"]: row for row in interval.get("threads", [])}
    result = []
    for identity in sorted(set(before["threads"]) | set(after["threads"])):
        initial, final = before["threads"].get(identity), after["threads"].get(identity)
        observed = final or initial
        delta = deltas.get(identity, {})
        cpu_id = final.get("processor") if final else None
        cpu_id = cpu_id if cpu_id is not None and cpu_id >= 0 else None
        previous = initial.get("processor") if initial else None
        previous = previous if previous is not None and previous >= 0 else None
        topology = bundle["topology_start"]["cpus"].get(f"cpu{cpu_id}", {})
        matches = [row for row in interval.get("perf_stat", {}).get("rows", []) if row.get("tid") == observed["tid"]]
        instructions = [row for row in matches if "instructions" in row["event"]]
        cycles = [row for row in matches if "cycles" in row["event"] and "instructions" not in row["event"]]
        valid = bool(initial and final and pmu_aligned(interval) and len(instructions) == len(cycles) == 1 and all(row.get("estimated_count") is not None and row.get("running_pct") is not None and 0 < row["running_pct"] <= 100 for row in matches))
        ratio = None
        if valid:
            ratios = [row["running_pct"] / 100 for row in matches]
            # PMU running/enabled is multiplex coverage. A sleeping thread's
            # event-enabled time is not a wall-window coverage denominator.
            ratio = min(ratios)
        result.append({"host_role": bundle["host_role"], "process_role": observed["process_role"],
                       "pool": observed["pool_role"], "branch": thread_branch(bundle["host_role"], observed["process_role"], observed["pool_role"]),
                       "pid": observed["pid"], "tid": observed["tid"], "starttime_ticks": observed["starttime_ticks"],
                       "name": observed.get("comm", ""), "state": final.get("state", "") if final else "exited",
                       "cpu_id": cpu_id, "previous_cpu_id": previous,
                       "allowed_cpu_ids": cpu_list(observed.get("allowed_cpus")), "numa_node": topology.get("numa_node"),
                       "cpu_pct": delta["cpu_pct"] if delta.get("cpu_pct") is not None and delta["cpu_pct"] <= 100 else None,
                       "instructions": round(instructions[0]["estimated_count"]) if valid else None,
                       "cycles": round(cycles[0]["estimated_count"]) if valid else None,
                       "counting_ratio": ratio,
                       "counter_scope": "same-window per-TID user+kernel grouped perf scaled estimate; endpoint identity verified" if valid else "unavailable: PMU window/identity/counter coverage incomplete",
                       "migrations": delta.get("migrations"), "runqueue_wait_ms": delta.get("runqueue_wait_ms")})
    return result


def merge_sample_windows(bundles, fio_samples, tolerance_s):
    timed = [bundle for bundle in bundles if bundle.get("observation") is not None]
    if not timed:
        if fio_samples is not None:
            raise ValueError("fio samples require collector observation/interval metadata")
        return None, []
    if len(timed) != len(bundles):
        raise ValueError("Cannot merge legacy and time-series collector bundles")
    reference = next((bundle for bundle in timed if bundle["host_role"] == "guest"), timed[0])
    observation = dict(reference["observation"])
    reference_intervals = reference["intervals"]
    if not reference_intervals or len(reference.get("samples", [])) != len(reference_intervals) + 1:
        raise ValueError("Collector samples/intervals are incomplete")
    for bundle in timed:
        if abs((timestamp(bundle["observation"]["started_at"]) - timestamp(bundle["started_at"])).total_seconds()) > tolerance_s or abs(bundle["observation"]["warmup_s"] + bundle["observation"]["measurement_s"] - bundle["duration_s"]) > tolerance_s:
            raise ValueError("Observation timing conflicts with capture lifecycle")
        if len(bundle.get("intervals", [])) != len(reference_intervals) or len(bundle.get("samples", [])) != len(reference_intervals) + 1:
            raise ValueError("Host sample-window counts differ")
        first_sample, last_sample = bundle["samples"][0], bundle["samples"][-1]
        if abs(first_sample["offset_s"]) > 1e-8 or abs(last_sample["offset_s"] - bundle["duration_s"]) > 1e-8 or abs((timestamp(first_sample["timestamp"]) - timestamp(bundle["started_at"])).total_seconds()) > tolerance_s or abs((timestamp(last_sample["timestamp"]) - timestamp(bundle["ended_at"])).total_seconds()) > tolerance_s:
            raise ValueError("Collector sample endpoints do not cover its capture lifecycle")
        if any(abs(bundle["observation"][key] - observation[key]) > tolerance_s for key in ("warmup_s", "measurement_s", "interval_s")):
            raise ValueError("Host observation phase durations differ")
        for index, (row, expected) in enumerate(zip(bundle["intervals"], reference_intervals)):
            if row.get("phase") != expected.get("phase") or any(abs(row[key] - expected[key]) > tolerance_s for key in ("start_offset_s", "end_offset_s")):
                raise ValueError("Host sample windows differ; reschedule a synchronized capture")
            if any(abs((timestamp(row[key]) - timestamp(expected[key])).total_seconds()) > tolerance_s for key in ("started_at", "ended_at")):
                raise ValueError("Host interval timestamps differ")
            before, after = bundle["samples"][index:index + 2]
            if abs(before["offset_s"] - row["start_offset_s"]) > 1e-8 or abs(after["offset_s"] - row["end_offset_s"]) > 1e-8:
                raise ValueError("Collector interval does not match its proc endpoints")
            if any(abs((timestamp(row[key]) - timestamp(sample["timestamp"])).total_seconds()) > tolerance_s for key, sample in (("started_at", before), ("ended_at", after))):
                raise ValueError("Collector interval timestamps do not match its proc endpoints")
            if row["end_offset_s"] <= row["start_offset_s"] or abs(row["duration_s"] - row["end_offset_s"] + row["start_offset_s"]) > 1e-8 or abs((timestamp(row["ended_at"]) - timestamp(row["started_at"])).total_seconds() - row["duration_s"]) > tolerance_s:
                raise ValueError("Collector interval duration conflicts with offsets/timestamps")
            boundary = bundle["observation"]["warmup_s"]
            if row["phase"] not in ("warmup", "measurement") or row["phase"] == "warmup" and row["end_offset_s"] > boundary + 1e-8 or row["phase"] == "measurement" and row["start_offset_s"] < boundary - 1e-8:
                raise ValueError("Collector interval crosses its observation phase boundary")
        formal = [row for row in bundle["intervals"] if row["phase"] == "measurement"]
        if not formal or abs((timestamp(formal[0]["started_at"]) - timestamp(bundle["measurement_started_at"])).total_seconds()) > tolerance_s or abs((timestamp(formal[-1]["ended_at"]) - timestamp(bundle["measurement_ended_at"])).total_seconds()) > tolerance_s:
            raise ValueError("Collector measurement endpoints conflict with its intervals")
    io_rows = [] if fio_samples is None else fio_samples
    if not isinstance(io_rows, list):
        raise ValueError("fio-samples-json must be a list of per-window guest counts")
    matched = {}
    previous_end = 0
    for row in io_rows:
        if not isinstance(row, dict) or set(row) - {"start_offset_s", "end_offset_s", "completed_ios", "latency_ms"}:
            raise ValueError("fio sample permits only start_offset_s/end_offset_s/completed_ios/latency_ms")
        start, end, completed = row.get("start_offset_s"), row.get("end_offset_s"), row.get("completed_ios")
        if not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or isinstance(start, bool) or isinstance(end, bool) or not math.isfinite(start) or not math.isfinite(end) or start < previous_end or end <= start or isinstance(completed, bool) or not isinstance(completed, int) or completed < 0:
            raise ValueError("fio sample requires ordered non-overlapping offsets and non-negative integer completed_ios")
        latency = row.get("latency_ms")
        if latency is not None and (isinstance(latency, bool) or not isinstance(latency, (int, float)) or not math.isfinite(latency) or latency < 0):
            raise ValueError("fio sample latency_ms must be non-negative or null")
        indices = [index for index, expected in enumerate(reference_intervals) if abs(start - expected["start_offset_s"]) <= tolerance_s and abs(end - expected["end_offset_s"]) <= tolerance_s]
        if len(indices) != 1 or indices[0] in matched:
            raise ValueError("fio sample does not align to a unique collector window")
        matched[indices[0]] = row
        previous_end = end
    windows = []
    for index, reference_interval in enumerate(reference_intervals):
        completed = matched.get(index, {}).get("completed_ios")
        duration = reference_interval["end_offset_s"] - reference_interval["start_offset_s"]
        window = {"start_offset_s": reference_interval["start_offset_s"], "end_offset_s": reference_interval["end_offset_s"],
                  "phase": reference_interval["phase"], "iops": completed / duration if completed is not None else None,
                  "completed_ios": completed, "latency_ms": matched.get(index, {}).get("latency_ms"),
                  "threads": [], "cpus": [], "network": [], "layers": []}
        for bundle in timed:
            interval = bundle["intervals"][index]
            before, after = bundle["samples"][index:index + 2]
            host = {**bundle, "duration_s": interval["duration_s"], "host_deltas": host_delta(before["host"], after["host"])}
            window["threads"].extend(individual_threads(bundle, before, after, interval))
            window["cpus"].extend(cpu_rows(host))
            window["network"].extend(network_rows(host))
        windows.append(window)
    return observation, windows


def build_batch(bundles, metadata, scenario, fio_payload=None, *, fio_window=None, fio_samples=None, tolerance_s=0.1):
    if not bundles:
        raise ValueError("At least one collector bundle required")
    if any(bundle.get("collector_schema") != 1 or bundle.get("source") != "measured" for bundle in bundles):
        raise ValueError("Only measured collector-schema 1 bundles can be merged")
    if len({bundle["run_id"] for bundle in bundles}) != 1:
        raise ValueError("Cannot merge different fullfio run IDs")
    roles = [bundle["host_role"] for bundle in bundles]
    if len(set(roles)) != len(roles) or not set(roles).issubset({"guest", "local", "remote"}):
        raise ValueError("Host roles must be unique guest/local/remote")
    windows = [_window(bundle) for bundle in bundles]
    starts = [window[0] for window in windows]
    ends = [window[1] for window in windows]
    if (max(starts) - min(starts)).total_seconds() > tolerance_s or (max(ends) - min(ends)).total_seconds() > tolerance_s:
        raise ValueError("Host capture windows differ; reschedule a synchronized capture")
    model = scenario["fio"]
    required_fio = ("rw", "bs", "iodepth", "numjobs", "vm_count", "ioengine", "direct")
    if any(key not in model for key in required_fio):
        raise ValueError("Scenario requires complete fio model")
    for bundle in bundles:
        if bundle.get("scenario") is not None and bundle["scenario"] != scenario:
            raise ValueError("Bundle and supplied scenario models disagree")
    guest = next((bundle for bundle in bundles if bundle["host_role"] == "guest"), None)
    summary = parse_fio_json(fio_payload) if fio_payload else guest.get("fio_summary") if guest else None
    if summary is None or any(summary.get(key) is None for key in ("iops", "bandwidth_mib", "latency_ms")):
        raise ValueError("fio summary requires valid IOPS, bandwidth and mean total latency for metric import")
    quality = [message for bundle in bundles for message in bundle.get("quality", [])]
    quality.extend(validate_fio_model(summary, model))
    quality.append("Cross-host timestamps depend on external clock synchronization; the collector does not certify NTP/PTP accuracy.")
    quality.append("Network packets/drop data are interface totals; IRQ rows do not claim queue-exclusive softIRQ time. Retransmits and per-queue packet counts are unknown.")
    missing_roles = {"guest", "local", "remote"} - set(roles)
    if missing_roles:
        quality.append("Missing host observations: " + ", ".join(sorted(missing_roles)))
    observation, sample_windows = merge_sample_windows(bundles, fio_samples, tolerance_s)
    denominator = None
    if observation:
        started = timestamp(observation["started_at"]) + timedelta(seconds=observation["warmup_s"])
        ended = started + timedelta(seconds=observation["measurement_s"])
        measurement_windows = [(timestamp(bundle["measurement_started_at"]), timestamp(bundle["measurement_ended_at"])) for bundle in bundles]
        formal = [row for row in sample_windows if row["phase"] == "measurement"]
        runtimes = [row.get("runtime_ms") for row in summary.get("jobs", [])]
        if not runtimes or any(not isinstance(runtime, (int, float)) or abs(runtime / 1000 - observation["measurement_s"]) > max(tolerance_s, observation["measurement_s"] * .01) for runtime in runtimes):
            raise ValueError("Time-series fio summary must cover measurement duration only; do not include warmup")
        if formal and all(row["completed_ios"] is not None for row in formal):
            denominator = sum(row["completed_ios"] for row in formal)
        quality.append("Time series preserves per-window individual PID/TID/starttime and endpoint CPU; warmup is excluded from pool/CPU/network summaries. Layer latency/depth is uncollected.")
        quality.append("Per-window IOPS uses external guest counts divided by actual reference window duration; missing counts stay null. Endpoint alignment tolerance does not certify clock synchronization or complete CPU residency.")
    else:
        started, ended = windows[0]
        measurement_windows = windows
    if fio_window is not None:
        started, ended = timestamp(fio_window["started_at"]), timestamp(fio_window["ended_at"])
        denominator = fio_window["completed_ios"]
        if ended <= started or isinstance(denominator, bool) or not isinstance(denominator, int) or denominator < 0:
            raise ValueError("fio-window requires an ordered window and non-negative integer completed_ios")
        if observation and all(row["completed_ios"] is not None for row in sample_windows if row["phase"] == "measurement") and fio_window["completed_ios"] != sum(row["completed_ios"] for row in sample_windows if row["phase"] == "measurement"):
            raise ValueError("fio-window completed_ios conflicts with measurement sample counts")
        if any(abs((start - started).total_seconds()) > tolerance_s or abs((end - ended).total_seconds()) > tolerance_s for start, end in measurement_windows):
            raise ValueError("fio completed-IO window does not match host capture windows")
        quality.append("Completed-IO window is supplied by external test orchestration; CLI validates timing consistency, not provenance of that assertion.")
    elif denominator is None:
        quality.append("MISSING_WINDOW_DENOMINATOR: fio JSON whole-run total_ios is not a collector-window count; per-IO costs must remain unknown.")
        quality.append("fio metrics describe the supplied whole-run summary; its alignment with collector observations is unverified.")
    if observation and fio_samples is None:
        quality.append("No per-window guest completion log supplied: time-series IOPS and latency remain null despite available measurement-only fio summary.")
    # Counter and CPU windows are separately measurable. Keep paired counters for
    # IPC, but deny per-IO cost if their window differs; backend quality gates use
    # this marker and the null denominator.
    if denominator is not None:
        for bundle in bundles:
            stat = bundle.get("perf_stat", {})
            if stat.get("started_at") and stat.get("ended_at"):
                if abs((timestamp(stat["started_at"]) - started).total_seconds()) > tolerance_s or abs((timestamp(stat["ended_at"]) - ended).total_seconds()) > tolerance_s:
                    denominator = None
                    quality.append("MISSING_WINDOW_DENOMINATOR: PMU counter window differs from guest completion window; per-IO normalization disabled, IPC still viewable.")
                    break
    threads = [row for bundle in bundles for row in bundle["threads"]]
    summaries = [{**bundle, "duration_s": bundle.get("measurement_duration_s", bundle["duration_s"])} for bundle in bundles]
    cpus = [row for bundle in summaries for row in cpu_rows(bundle)]
    network = [row for bundle in summaries for row in network_rows(bundle)]
    inventory = [row for bundle in bundles for row in cpu_inventory_rows(bundle)]
    for bundle in bundles:
        topology_known = any(topology.get("online_cpu_ids") is not None for topology in (bundle.get("topology_start", {}), bundle.get("topology_end", {})))
        proc_known = any(sample.get("host", {}).get("available", {}).get("stat") is True and sample.get("host", {}).get("cpu") for sample in bundle.get("samples", []))
        if not topology_known and not proc_known:
            quality.append(f"CPU_INVENTORY_RANGE_UNKNOWN: {bundle['host_role']} inventory includes only observed CPU IDs; complete online CPU range could not be certified.")
    if observation:
        expected = {(row["host_role"], row["cpu_id"]) for row in inventory}
        missing = sum(bool(expected - {(row["host_role"], row["cpu_id"]) for row in window["cpus"]}) for window in sample_windows)
        if missing:
            quality.append(f"CPU_INCOMPLETE_WINDOWS: {missing} sample windows omit logical CPUs with missing/reset/non-advancing counters; inventory retains expected scope and missing values must not be filled with zero.")
        quality.append("CPU inventory is the union of observed /proc/stat CPU IDs and explicit /sys online CPU lists; a missing observation is unknown, not idle. CPU hotplug/state changes are not a complete residency trace.")
    artifacts = [artifact for bundle in bundles for artifact in bundle.get("artifacts", [])]
    environment = metadata["environment"]
    # Environment fingerprint must be supplied as a stable explicit archive;
    # observations (CPU load, mutable process IDs) are never used as its identity.
    if not all(key in environment for key in ("id", "label", "description")):
        raise ValueError("Metadata environment requires stable id, label and description")
    return {"schema_version": 1, "id": metadata["id"], "title": metadata["title"], "build": metadata["build"],
            "created_at": metadata.get("created_at") or ended.isoformat(), "source": "measured",
            "environment": environment, "changes": metadata.get("changes", []),
            "scenarios": [{"id": scenario["id"], "title": scenario["title"], "protocol": scenario["protocol"], "fio": model,
                           "window": {"duration_s": (ended - started).total_seconds(), "started_at": started.isoformat(), "completed_ios": denominator},
                           "metrics": {"iops": summary["iops"], "bandwidth_mib_s": summary["bandwidth_mib"], "latency_ms": summary["latency_ms"], "p99_ms": None},
                           "timeline": [], "threads": threads, "cpus": cpus, "network": network,
                           "observation": observation, "sample_windows": sample_windows,
                           "cpu_inventory": inventory,
                           "quality": list(dict.fromkeys(quality)), "artifacts": artifacts}]}


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))
