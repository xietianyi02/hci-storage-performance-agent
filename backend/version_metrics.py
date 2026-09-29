"""Pure counter arithmetic for aligned version-test sampling windows.

None means unavailable, never zero. Thread-group IPC is counter-weighted;
CPU cost is normalized by completed VM fio IOs rather than CPU-percent alone.
"""

import hashlib
import json


def stable_key(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")).hexdigest()


def percent_change(current, previous):
    if current is None or previous is None or previous == 0:
        return None
    return round((current / previous - 1) * 100, 3)


def weighted_ipc(rows):
    """Aggregate matching instructions/cycles scopes, excluding missing pairs."""
    available = [row for row in rows if row.get("instructions") is not None and row.get("cycles") is not None and row["cycles"] > 0]
    if not available:
        return None
    return sum(row["instructions"] for row in available) / sum(row["cycles"] for row in available)


def cpu_cost_us_per_io(rows, completed_ios):
    if completed_ios is None or completed_ios <= 0 or not rows or any(row.get("cpu_seconds") is None for row in rows):
        return None
    return sum(row["cpu_seconds"] for row in rows) * 1_000_000 / completed_ios


def rate(counter_delta, duration_sec):
    if counter_delta is None or duration_sec is None or duration_sec <= 0:
        return None
    return counter_delta / duration_sec


def derive_thread(capture, window):
    if capture is None:
        return None
    duration = window["duration_s"]
    completed = window.get("completed_ios")
    cpu = capture.get("cpu_time_ms")
    instructions, cycles = capture.get("instructions"), capture.get("cycles")
    scope = capture.get("counter_scope", "").strip().lower()
    ratio = capture.get("counting_ratio")
    pmu_valid = instructions is not None and cycles is not None and cycles > 0 and ratio is not None and ratio >= 0.9 and scope not in ("", "unknown", "unavailable", "not_collected")
    return {
        "cpu_pct": cpu / (duration * 1000) * 100 if cpu is not None else None,
        "cpu_us_per_io": cpu * 1000 / completed if cpu is not None and completed and completed > 0 else None,
        "ipc": instructions / cycles if pmu_valid else None,
        "instructions_per_io": instructions / completed if pmu_valid and completed and completed > 0 else None,
        "cycles_per_io": cycles / completed if pmu_valid and completed and completed > 0 else None,
    }
