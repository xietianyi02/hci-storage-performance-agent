"""Pure parsers: unavailable counters are None, never fabricated zeros."""

import math
import re


def number(value):
    try:
        result = float(str(value).strip())
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def parse_task_stat(text):
    # comm may contain spaces or ')' characters; fields start after its final ')'.
    end = text.rfind(")")
    begin = text.find("(")
    if begin < 0 or end < begin:
        raise ValueError("Invalid /proc task stat")
    parts = text[end + 1:].split()
    if len(parts) < 37:
        raise ValueError("Incomplete /proc task stat")
    return {
        "tid": int(text[:begin].strip()), "comm": text[begin + 1:end], "state": parts[0],
        "utime_ticks": int(parts[11]), "stime_ticks": int(parts[12]),
        "starttime_ticks": int(parts[19]), "processor": int(parts[36]),
    }


def parse_status(text):
    pairs = {}
    for line in text.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            pairs[key] = value.strip()
    return {
        "allowed_cpus": pairs.get("Cpus_allowed_list"),
        "allowed_mems": pairs.get("Mems_allowed_list"),
        "voluntary_switches": int(pairs["voluntary_ctxt_switches"]) if "voluntary_ctxt_switches" in pairs else None,
        "involuntary_switches": int(pairs["nonvoluntary_ctxt_switches"]) if "nonvoluntary_ctxt_switches" in pairs else None,
    }


def parse_schedstat(text):
    fields = text.split()
    if len(fields) < 3:
        return {"runtime_ns": None, "runqueue_wait_ns": None, "timeslices": None}
    return dict(zip(("runtime_ns", "runqueue_wait_ns", "timeslices"), map(int, fields[:3])))


def parse_numa_maps(text):
    pages = {}
    for node, value in re.findall(r"\bN(\d+)=(\d+)\b", text):
        key = "node" + node
        pages[key] = pages.get(key, 0) + int(value)
    return pages


def parse_cpu_stat(text):
    result = {}
    names = ("user", "nice", "system", "idle", "iowait", "irq", "softirq", "steal")
    for line in text.splitlines():
        parts = line.split()
        if parts and re.fullmatch(r"cpu\d+", parts[0]) and len(parts) >= 9:
            result[parts[0]] = dict(zip(names, map(int, parts[1:9])))
    return result


def parse_interrupts(text):
    lines = text.splitlines()
    if not lines:
        return {}
    cpus = re.findall(r"CPU\d+", lines[0])
    result = {}
    for line in lines[1:]:
        key, sep, remainder = line.partition(":")
        parts = remainder.split()
        if not sep or len(parts) < len(cpus) or not cpus:
            continue
        try:
            counts = [int(value) for value in parts[:len(cpus)]]
        except ValueError:
            continue
        result[key.strip()] = {"counts": dict(zip((cpu.lower() for cpu in cpus), counts)),
                               "description": " ".join(parts[len(cpus):])}
    return result


def counter_delta(before, after):
    if before is None or after is None or after < before:
        return None
    return after - before


def cpu_delta(before, after):
    result = {}
    for cpu in sorted(set(before) & set(after)):
        values = {key: counter_delta(before[cpu].get(key), value) for key, value in after[cpu].items()}
        total = sum(values.values()) if all(value is not None for value in values.values()) else None
        result[cpu] = {"delta_ticks": values, "total_ticks": total,
                       "busy_pct": round(100 * (total - values["idle"] - values["iowait"]) / total, 3) if total else None}
    return result


def interrupt_delta(before, after):
    result = {}
    for key, item in after.items():
        if key not in before:
            continue
        result[key] = {"description": item["description"], "delta_count_by_cpu": {
            cpu: counter_delta(before[key]["counts"].get(cpu), count) for cpu, count in item["counts"].items()
        }}
    return result


def parse_perf_stat(text):
    """Parse perf stat --per-thread -x ';' --no-scale output (LC_ALL=C).

    Runtime is ns in perf CSV. The counter remains raw; explicit scaling is
    estimated from runtime percentage, and low/unknown coverage stays visible.
    PMU-prefixed hybrid events are kept separate rather than incorrectly merged.
    """
    rows = []
    for line in text.splitlines():
        cells = [value.strip() for value in line.split(";")]
        event_index = next((i for i, value in enumerate(cells) if re.search(r"(?:^|/)(instructions|cycles)(?:$|[:/])", value)), None)
        if event_index is None or event_index < 2:
            continue
        label = cells[event_index - 3] if event_index >= 3 else None
        value = number(cells[event_index - 2])
        event = cells[event_index]
        runtime_ns = number(cells[event_index + 1]) if len(cells) > event_index + 1 else None
        running_pct = number(cells[event_index + 2].rstrip("%")) if len(cells) > event_index + 2 else None
        tid_match = re.search(r"-(\d+)$", label or "")
        coverage = running_pct / 100 if running_pct is not None and running_pct > 0 else None
        rows.append({"tid": int(tid_match.group(1)) if tid_match else None, "label": label,
                     "event": event, "raw_count": value, "estimated_count": value / coverage if value is not None and coverage else None,
                     "time_running_ns": runtime_ns,
                     "time_enabled_ns": runtime_ns / coverage if runtime_ns is not None and coverage else None,
                     "running_pct": running_pct, "status": "ok" if value is not None else "unavailable"})
    return rows


def parse_fio_json(payload):
    jobs = payload.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise ValueError("fio JSON requires jobs[]")
    totals = {"iops": 0.0, "bandwidth_mib": 0.0, "completed_ios": 0, "latency_ns_weighted": 0.0, "latency_weight": 0}
    details = []
    for job in jobs:
        for direction in ("read", "write", "trim"):
            item = job.get(direction) or {}
            completed = item.get("total_ios")
            ios = int(completed) if number(completed) is not None and number(completed) >= 0 else None
            if ios is None or ios == 0:
                continue
            iops = number(item.get("iops"))
            bandwidth = number(item.get("bw_bytes"))
            if bandwidth is None and number(item.get("bw")) is not None:
                bandwidth = number(item["bw"]) * 1024  # fio bw is KiB/s.
            latency_ns = None
            for key, multiplier in (("lat_ns", 1), ("lat_us", 1000), ("lat_ms", 1000000)):
                mean = number((item.get(key) or {}).get("mean"))
                if mean is not None:
                    latency_ns = mean * multiplier
                    break
            if iops is not None:
                totals["iops"] += iops
            if bandwidth is not None:
                totals["bandwidth_mib"] += bandwidth / 1048576
            totals["completed_ios"] += ios
            if latency_ns is not None:
                totals["latency_ns_weighted"] += latency_ns * ios
                totals["latency_weight"] += ios
            details.append({"job": job.get("jobname"), "direction": direction, "completed_ios": ios,
                            "iops": iops, "bandwidth_mib": bandwidth / 1048576 if bandwidth is not None else None,
                            "latency_ms": latency_ns / 1000000 if latency_ns is not None else None,
                            "runtime_ms": item.get("runtime")})
    if not details:
        raise ValueError("fio JSON has no completed IO counts; normalization cannot be inferred")
    return {"source": "imported", "iops": totals["iops"] if all(item["iops"] is not None for item in details) else None,
            "bandwidth_mib": totals["bandwidth_mib"] if all(item["bandwidth_mib"] is not None for item in details) else None,
            "latency_ms": totals["latency_ns_weighted"] / totals["latency_weight"] / 1000000 if totals["latency_weight"] == totals["completed_ios"] else None,
            "completed_ios": totals["completed_ios"], "latency_kind": "mean_total_latency", "jobs": details,
            "job_options": [job.get("job options", {}) for job in jobs],
            "global_options": payload.get("global options", {}),
            "note": "fio JSON summary; simultaneous jobs assumed for summed IOPS. Counts cover fio's own complete run, not an arbitrary collector subwindow."}
