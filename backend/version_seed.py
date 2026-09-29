"""Explicitly synthetic historical test batches; never used as measured baselines."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from .version_timeseries_seed import attach_mock_observations


ENVIRONMENT = {
    "id": "poc-fixed-x86-3n-demo",
    "label": "POC 固定环境 · 三节点 / 两台数据主机",
    "description": "MOCK：固定 x86 硬件与集群；数据仅保存到本地和一个远端。版本与每轮软件、线程及网络参数单独记录。",
}


def pool(host, process, name, branch, count, cpu_ms, io_count, cpu_ids, *, wait=1500, remote_pct=4):
    # Pool counters are already summed over the entire declared pool/window.
    cycles = round(cpu_ms * 3_000_000)
    return {"host_role": host, "process_role": process, "pool": name, "branch": branch,
            "thread_count": count, "cpu_time_ms": cpu_ms, "instructions": round(cycles * 1.15), "cycles": cycles,
            "runqueue_wait_ms": wait, "context_switches": round(io_count * .035), "migrations": 18,
            "cpu_ids": cpu_ids, "allowed_cpu_ids": cpu_ids, "numa_nodes": [0], "memory_numa_nodes": [0],
            "remote_access_pct": remote_pct, "counting_ratio": .98, "counter_scope": "thread_pool_all_threads:user+kernel:scaled",
            "top_functions": [{"name": "ior_dispatch" if process == "asan-stord" else "io_callback", "samples_pct": 28.2}]}


def cpu(host, cpu_id, *, user=26, system=18, irq=2, softirq=4, competitors=None, numa=0):
    return {"host_role": host, "cpu_id": cpu_id, "numa_node": numa, "user_pct": user, "system_pct": system,
            "irq_pct": irq, "softirq_pct": softirq, "idle_pct": max(0, 100 - user - system - irq - softirq),
            "competitors": competitors or []}


def scenario(identifier, title, rw, depth, jobs, iops, latency, *, cpu_regression=False, nic_regression=False, day=28):
    duration = 60
    completed = round(iops * duration)
    write = "write" in rw
    threads = [
        pool("local", "qemu", "main-loop", "protocol", 1, 24000, completed, [4]),
        pool("local", "asan-stord", "gfapi-opt", "business", 2, 32000, completed, [8, 9], wait=19000 if cpu_regression else 1500),
        pool("local", "glusterfsd", "glfsd-net", "local-data", 2, 18000, completed, [10, 11]),
        pool("local", "asan-stord", "tierd-core", "local-data", 4, 48000, completed, [12, 13, 14, 15]),
    ]
    if write:
        threads.append(pool("remote", "asan-stord", "tierd-core", "remote-data", 4, 45000, completed, [12, 13, 14, 15]))
        threads.append(pool("remote", "glusterfsd", "glfsd-net", "remote-data", 2, 17000, completed, [10, 11]))
    if cpu_regression:
        threads[1].update(cpu_time_ms=44000, instructions=126000000000, cycles=144000000000,
                          cpu_ids=[40, 41], allowed_cpu_ids=[40, 41], numa_nodes=[1], memory_numa_nodes=[0], remote_access_pct=31, migrations=640)
    cpus = [cpu("local", 4), cpu("local", 8, user=30), cpu("local", 9, user=28),
            cpu("local", 40, user=68 if cpu_regression else 18, system=24 if cpu_regression else 8,
                competitors=["背景压缩任务（模拟）"] if cpu_regression else [], numa=1),
            cpu("local", 41, user=61 if cpu_regression else 16, system=25 if cpu_regression else 8, numa=1),
            cpu("local", 24, irq=5 if nic_regression else 2, softirq=24 if nic_regression else 4),
            cpu("remote", 24, irq=9 if nic_regression else 2, softirq=39 if nic_regression else 5)]
    network = [
        {"host_role": "local", "interface": "storage0", "queue": "rx-0", "irq_id": 120, "cpu_ids": [24], "allowed_cpu_ids": [24], "numa_node": 0,
         "irq_per_s": 96000 if nic_regression else 54000, "packets_per_s": iops * (1.8 if write else .15), "drops": 930 if nic_regression else 0, "retransmits": 210 if nic_regression else 0},
        {"host_role": "remote", "interface": "storage0", "queue": "rx-0", "irq_id": 120, "cpu_ids": [24], "allowed_cpu_ids": [24], "numa_node": 0,
         "irq_per_s": 119000 if nic_regression else 55000, "packets_per_s": iops * (2.2 if write else .12), "drops": 4800 if nic_regression else 0, "retransmits": 860 if nic_regression else 0},
    ]
    snapshot = {"id": identifier, "title": title, "protocol": "nfs",
            "fio": {"rw": rw, "bs": "4k", "iodepth": depth, "numjobs": jobs, "vm_count": 1, "ioengine": "libaio", "direct": 1},
            "window": {"duration_s": duration, "started_at": f"2026-09-{day:02d}T01:00:00+00:00", "completed_ios": completed},
            "metrics": {"iops": iops, "bandwidth_mib_s": round(iops * 4096 / 1048576, 2), "latency_ms": latency, "p99_ms": round(latency * 2.4, 3)},
            "timeline": [{"offset_s": offset, "iops": round(iops * factor), "latency_ms": round(latency / factor, 3)} for offset, factor in [(0, .98), (15, 1.015), (30, .992), (45, 1.009), (60, 1.)]],
            "threads": threads, "cpus": cpus, "network": network,
            "quality": ["MOCK：所有指标与拓扑均为演示样本。", "示例完成数与采集窗口已对齐；真实导入必须核验。"],
            "artifacts": [{"kind": "perf-stat", "path": f"mock://version/{day}/{identifier}/perf-stat.json", "note": "仅示例引用；服务不会读取该路径。"}]}
    return attach_mock_observations(snapshot, cpu_regression=cpu_regression, nic_regression=nic_regression)


def seed_batches():
    batches = []
    for day in (27, 28, 29):
        latest = day == 29
        batch = {"schema_version": 1, "id": f"VB-DEMO-{day}", "title": f"09/{day} 全 CPU · 全模型版本回归（模拟）", "build": f"HCI 6.1.0-build.{day}",
                 "created_at": f"2026-09-{day}T02:00:00+00:00", "source": "mock", "environment": deepcopy(ENVIRONMENT),
                 "changes": ["模拟变更线索：业务线程调度配置", "模拟变更线索：存储网络驱动参数"] if latest else ["模拟历史版本基线"],
                 "scenarios": [
                     scenario("nfs-low-read", "NFS · 低深度 4K 随机读", "randread", 1, 1, 22800 if latest else 28000, .043 if latest else .036, cpu_regression=latest, day=day),
                     scenario("nfs-high-read", "NFS · 高深度 4K 随机读", "randread", 64, 8, 181500 if latest else 180000, .82 if latest else .83, day=day),
                     scenario("nfs-low-write", "NFS · 低深度 4K 随机写", "randwrite", 1, 1, 19600 if latest else 20000, .052 if latest else .051, day=day),
                     scenario("nfs-high-write", "NFS · 高深度 4K 随机写", "randwrite", 64, 8, 102000 if latest else 130000, 1.28 if latest else 1.01, nic_regression=latest, day=day),
                 ]}
        batches.append(batch)
    return batches


def import_example():
    batch = deepcopy(seed_batches()[-1])
    batch["id"] = "VB-IMPORT-" + uuid4().hex[:10]
    batch["title"] = "可导入的 MOCK 测试批次"
    batch["created_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for sample in batch["scenarios"]:
        sample["window"]["started_at"] = batch["created_at"]
        # Keep the preheat origin aligned when cloning a newly timestamped example.
        sample["observation"]["started_at"] = (datetime.fromisoformat(batch["created_at"]) - timedelta(seconds=30)).isoformat()
    return batch
