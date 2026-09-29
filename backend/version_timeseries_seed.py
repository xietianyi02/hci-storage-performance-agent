"""Synthetic 3-second observations for the demo, never measured evidence."""

from copy import deepcopy
from datetime import datetime, timedelta
import math


MOCK_CPU_COUNTS = {"guest": 8, "local": 128, "remote": 128}


def mock_cpu_inventory():
    """Explicit demo topology; these counts never describe a connected lab."""
    rows = []
    for host, count in MOCK_CPU_COUNTS.items():
        for cpu_id in range(count):
            core = cpu_id if host == "guest" else cpu_id % 64
            rows.append({"host_role": host, "cpu_id": cpu_id, "numa_node": 0 if host == "guest" else core // 32,
                         "socket_id": 0, "core_id": core,
                         "smt_sibling_cpu_ids": [cpu_id] if host == "guest" else [core, core + 64]})
    return rows


def mock_cpu_background(topology, index, measuring):
    cpu_id, host = topology["cpu_id"], topology["host_role"]
    busy = 4 + (cpu_id * 7 + index * 3) % (16 if host == "guest" else 12)
    competitors = []
    if host == "local" and cpu_id in (31, 95) and measuring and 17 <= index <= 23:
        busy = 88 if cpu_id == 31 else 62
        competitors = ["宿主后台任务（模拟）"]
    return {**topology, "user_pct": busy - 3, "system_pct": 2, "irq_pct": .5, "softirq_pct": .5,
            "idle_pct": 100 - busy, "busy_pct": busy, "competitors": competitors}


def attach_mock_observations(snapshot, *, cpu_regression=False, nic_regression=False):
    """Keep summary and individual-window simulated counters independently explicit."""
    warmup, measurement, interval = 30, 60, 3
    origin = datetime.fromisoformat(snapshot["window"]["started_at"]) - timedelta(seconds=warmup)
    snapshot["observation"] = {"started_at": origin.isoformat(), "warmup_s": warmup,
                               "measurement_s": measurement, "interval_s": interval}
    factors = [1 + .025 * math.sin(index * .8) - (.14 if (cpu_regression or nic_regression) and 7 <= index <= 13 else 0)
               for index in range(20)]
    average = sum(factors) / len(factors)
    factors = [value / average for value in factors]
    cpu_factors = [1 + .05 * math.sin(index * .7) for index in range(20)]
    cpu_average = sum(cpu_factors) / len(cpu_factors)
    cpu_factors = [value / cpu_average for value in cpu_factors]
    windows = []
    inventory = mock_cpu_inventory()
    snapshot["cpu_inventory"] = inventory
    previous_cpus = {}
    measured_ios = snapshot["window"]["completed_ios"]
    cumulative_ios = 0
    weighted_ios = 0
    for index in range(30):
        measuring = index >= 10
        point = index - 10
        factor = factors[point] if measuring else .45 + index * .052
        if measuring:
            weighted_ios += factors[point]
            next_ios = round(measured_ios * weighted_ios / 20)
            completed_ios = next_ios - cumulative_ios
            cumulative_ios = next_ios
        else:
            completed_ios = round(snapshot["metrics"]["iops"] * interval * factor)
        cpus = {(row["host_role"], row["cpu_id"]): mock_cpu_background(row, index, measuring) for row in inventory}
        for base in snapshot["cpus"]:
            capture = deepcopy(base)
            identifier = capture["cpu_id"]
            sibling = identifier + 64 if identifier < 64 else identifier - 64
            capture.update(socket_id=0, core_id=min(identifier, sibling), smt_sibling_cpu_ids=sorted([identifier, sibling]),
                           busy_pct=round(100 - capture["idle_pct"], 2))
            cpus[(capture["host_role"], identifier)] = capture
        thread_samples = []
        for pool_index, pool in enumerate(snapshot["threads"]):
            count = pool["thread_count"]
            weights = list(range(count, 0, -1))
            denominator = sum(weights)
            for number in range(count):
                identity = (pool["host_role"], pool_index, number)
                identifier = pool["cpu_ids"][number % len(pool["cpu_ids"])]
                is_business = cpu_regression and pool["pool"] == "gfapi-opt"
                if is_business and (not measuring or point < 7):
                    identifier = 8 + number % 2
                pid = (10000 if pool["host_role"] == "local" else 20000) + pool_index * 100
                tid = pid + number + 1
                share = weights[number] / denominator
                cpu_factor = cpu_factors[point] if measuring else factor
                cpu_pct = pool["cpu_time_ms"] / 600 * share * cpu_factor
                cycles = round(pool["cycles"] / 20 * share * cpu_factor)
                ipc = pool["instructions"] / pool["cycles"] if pool["cycles"] else 1
                if is_business:
                    # A visibly changing IPC is an explicitly simulated signal.
                    raw = [1.15 if item < 7 else .76 for item in range(20)]
                    weighted = sum(raw[item] * cpu_factors[item] for item in range(20)) / 20
                    ipc *= (raw[point] / weighted if measuring else 1.15 / weighted)
                node = 1 if identifier >= 32 else 0
                sibling = identifier + 64 if identifier < 64 else identifier - 64
                for cpu_id in (identifier, sibling):
                    key = (pool["host_role"], cpu_id)
                    if key not in cpus or cpu_id == sibling and is_business and measuring and point >= 7:
                        busy = 84 if cpu_id == sibling and is_business and measuring and point >= 7 else 14 if cpu_id == sibling else min(98, cpu_pct + 13)
                        cpus[key] = {"host_role": pool["host_role"], "cpu_id": cpu_id, "numa_node": node,
                                     "user_pct": round(busy - 6, 2), "system_pct": 4, "irq_pct": 1, "softirq_pct": 1,
                                     "idle_pct": round(100 - busy, 2), "busy_pct": round(busy, 2),
                                     "socket_id": 0, "core_id": min(cpu_id, sibling if cpu_id == identifier else identifier),
                                     "smt_sibling_cpu_ids": sorted([identifier, sibling]),
                                     "competitors": ["同物理核背景任务（模拟）"] if busy == 84 else []}
                previous = previous_cpus.get(identity, identifier)
                thread_samples.append({"host_role": pool["host_role"], "process_role": pool["process_role"],
                                       "pool": pool["pool"], "branch": pool["branch"], "pid": pid, "tid": tid,
                                       "starttime_ticks": pid * 10, "name": pool["pool"] + "-" + str(number), "state": "R",
                                       "cpu_id": identifier, "previous_cpu_id": previous,
                                       "allowed_cpu_ids": [8, 9, 40, 41] if is_business else pool["allowed_cpu_ids"],
                                       "numa_node": node, "cpu_pct": round(cpu_pct, 3),
                                       "instructions": round(cycles * ipc), "cycles": cycles,
                                       "counting_ratio": .98, "counter_scope": "mock:thread:user+kernel:same-window:scaled",
                                       "migrations": 1 if previous != identifier else 0,
                                       "runqueue_wait_ms": round(pool["runqueue_wait_ms"] / 20 * share * (1.7 if is_business and measuring and point >= 7 else .5), 3)})
                previous_cpus[identity] = identifier
        target_load = {}
        for thread in thread_samples:
            key = (thread["host_role"], thread["cpu_id"])
            target_load[key] = target_load.get(key, 0) + thread["cpu_pct"]
        for key, load in target_load.items():
            capture = cpus[key]
            required = min(99, load + capture["system_pct"] + capture["irq_pct"] + capture["softirq_pct"])
            if required > capture["busy_pct"]:
                capture["busy_pct"] = round(required, 3)
                capture["user_pct"] = round(required - capture["system_pct"] - capture["irq_pct"] - capture["softirq_pct"], 3)
                capture["idle_pct"] = round(100 - required, 3)
        network = deepcopy(snapshot["network"])
        for row in network:
            row["packets_per_s"] = round(row["packets_per_s"] * factor, 2)
            row["drops"] = round(row["drops"] * (point + 1) / 20) - round(row["drops"] * point / 20) if measuring else 0
            row["retransmits"] = round(row["retransmits"] * (point + 1) / 20) - round(row["retransmits"] * point / 20) if measuring else 0
            cpu = cpus.get((row["host_role"], 24))
            if nic_regression and measuring and 7 <= point <= 13 and cpu:
                cpu["softirq_pct"] = min(65, cpu["softirq_pct"] + 13)
                cpu["user_pct"] = max(0, 100 - cpu["system_pct"] - cpu["irq_pct"] - cpu["softirq_pct"] - 3)
                cpu["idle_pct"] = 3
                cpu["busy_pct"] = 97
        windows.append({"start_offset_s": index * interval, "end_offset_s": (index + 1) * interval,
                        "phase": "measurement" if measuring else "warmup", "completed_ios": completed_ios,
                        "iops": round(completed_ios / interval, 3), "latency_ms": round(snapshot["metrics"]["latency_ms"] / factor, 5),
                        "threads": thread_samples, "cpus": list(cpus.values()), "network": network, "layers": []})
    snapshot["sample_windows"] = windows
    formal = [window for window in windows if window["phase"] == "measurement"]
    rows_by_cpu = {(row["host_role"], row["cpu_id"]): [] for row in inventory}
    for window in formal:
        for row in window["cpus"]:
            rows_by_cpu[(row["host_role"], row["cpu_id"])].append(row)
    summary = []
    for topology in inventory:
        key = (topology["host_role"], topology["cpu_id"])
        observed = rows_by_cpu[key]
        summary.append({**topology, **{metric: round(sum(row[metric] for row in observed) / len(observed), 3)
                                      for metric in ("user_pct", "system_pct", "irq_pct", "softirq_pct", "idle_pct", "busy_pct")},
                        "competitors": sorted({task for row in observed for task in row["competitors"]})})
    snapshot["cpus"] = summary
    for pool in snapshot["threads"]:
        observed = [thread for window in windows if window["phase"] == "measurement" for thread in window["threads"]
                    if (thread["host_role"], thread["process_role"], thread["pool"], thread["branch"])
                    == (pool["host_role"], pool["process_role"], pool["pool"], pool["branch"])]
        pool["cpu_ids"] = sorted({thread["cpu_id"] for thread in observed})
        pool["allowed_cpu_ids"] = sorted({cpu for thread in observed for cpu in thread["allowed_cpu_ids"]})
        pool["numa_nodes"] = sorted({thread["numa_node"] for thread in observed})
    snapshot["timeline"] = [{"offset_s": row["end_offset_s"] - warmup, "iops": row["iops"], "latency_ms": row["latency_ms"]}
                            for row in windows if row["phase"] == "measurement"]
    snapshot["quality"].append("MOCK：3 秒分窗、具体线程身份、迁移与 SMT 负载均为模拟；各层时延和深度尚未接入。")
    snapshot["quality"].append("MOCK：CPU 采集清单为 guest 8 vCPU、本地与远端各 128 逻辑 CPU；每窗全量 264 条 CPU 观测，包含未运行目标线程的 CPU。这是明确模拟的配置，非已连接环境硬件。")
    return snapshot
