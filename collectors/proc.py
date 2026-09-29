"""Read-only /proc and /sys adapters, with injectable roots for offline tests."""

from pathlib import Path
import os
import re

from .parsers import parse_cpu_stat, parse_interrupts, parse_numa_maps, parse_schedstat, parse_status, parse_task_stat


DEFAULT_ROLES = [
    {"pattern": r"^fio$", "role": "fio", "hosts": ["guest"]},
    {"pattern": r"^(kvm|qemu.*)$", "role": "qemu", "hosts": ["local"]},
    {"pattern": r"^asan-stord$", "role": "stord", "hosts": ["local", "remote"]},
    {"pattern": r"^glusterfsd$", "role": "glusterfsd", "hosts": ["local", "remote"]},
]


def read_text(path, limit=2097152):
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            return None  # A truncated snapshot must not appear complete.
        return data.decode("utf-8", errors="replace").strip()
    except (OSError, PermissionError):
        return None


def pool_role(comm, process_role):
    for pattern, role in (
        (r"^gfapi-opt", "gfapi-opt"), (r"^gfapi-core", "gfapi-core"), (r"^gfapi-net", "gfapi-net"),
        (r"^glfs-nfs", "nfs-rpc"), (r"^glfsd-net", "glfsd-net"), (r"^libcomm", "libcomm"),
        (r"^tierd-core", "tierd-core"), (r"^tierd-aio", "tierd-aio"),
        (r"CPU.*KVM", "qemu-vcpu"), (r"(?i)nfs.*poll", "nfs-poller"),
    ):
        if re.search(pattern, comm):
            return role
    return "fio" if process_role == "fio" else "main" if comm in ("asan-stord", "kvm", "glusterfsd") or comm.startswith("qemu") else "unmapped"


def parse_cpu_list(value):
    if value is None:
        return None
    result = set()
    for segment in value.split(","):
        match = re.fullmatch(r"\s*(\d+)(?:-(\d+))?\s*", segment)
        if match is None:
            return None
        start, end = int(match[1]), int(match[2] or match[1])
        if start > end or end > 1048576:
            return None
        result.update(range(start, end + 1))
    return sorted(result)


class ProcReader:
    def __init__(self, host_role, *, proc_root="/proc", sys_root="/sys", roles=None):
        self.proc = Path(proc_root)
        self.sys = Path(sys_root)
        self.host_role = host_role
        self.roles = roles if roles is not None else DEFAULT_ROLES
        self.hz = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def discover(self):
        threads = {}
        processes = {}
        if not self.proc.is_dir():
            return threads, processes
        for folder in sorted(self.proc.iterdir()):
            if not folder.name.isdecimal():
                continue
            comm = read_text(folder / "comm")
            if comm is None:
                continue
            match = next((rule for rule in self.roles if self.host_role in rule.get("hosts", ["guest", "local", "remote"]) and re.search(rule["pattern"], comm)), None)
            if match is None:
                continue
            pid = int(folder.name)
            processes[str(pid)] = {"pid": pid, "comm": comm, "process_role": match["role"],
                                  "numa_resident_pages": parse_numa_maps(read_text(folder / "numa_maps") or ""),
                                  "numa_maps_available": read_text(folder / "numa_maps") is not None,
                                  "cgroup": read_text(folder / "cgroup")}
            task_root = folder / "task"
            try:
                tasks = list(task_root.iterdir())
            except OSError:
                continue
            for task in tasks:
                if not task.name.isdecimal():
                    continue
                stat = read_text(task / "stat")
                if not stat:
                    continue
                try:
                    item = parse_task_stat(stat)
                    status = read_text(task / "status")
                    sched = read_text(task / "schedstat")
                    item.update(parse_status(status or "") | parse_schedstat(sched or ""), pid=pid,
                                process_role=match["role"], pool_role=pool_role(item["comm"], match["role"]),
                                status_available=status is not None, schedstat_available=sched is not None)
                    sched = read_text(task / "sched") or ""
                    migration = re.search(r"se\.nr_migrations\s*:\s*(\d+)", sched)
                    item["migrations"] = int(migration.group(1)) if migration else None
                except (ValueError, IndexError):
                    continue  # Exited midway, rediscovered at next interval if alive.
                item["identity"] = f"{pid}:{item['tid']}:{item['starttime_ticks']}"
                threads[item["identity"]] = item
        return threads, processes

    def host_snapshot(self):
        values = {name: read_text(self.proc / name) for name in ("stat", "interrupts", "softirqs", "uptime")}
        return {"cpu": parse_cpu_stat(values["stat"] or ""), "interrupts": parse_interrupts(values["interrupts"] or ""),
                "softirqs": parse_interrupts(values["softirqs"] or ""), "uptime": values["uptime"],
                "available": {key: value is not None for key, value in values.items()},
                "network": self.network_counters()}

    def network_counters(self):
        interfaces = {}
        for device in sorted((self.sys / "class/net").glob("*")):
            values = {}
            for name in ("rx_packets", "tx_packets", "rx_bytes", "tx_bytes", "rx_dropped", "tx_dropped", "rx_errors", "tx_errors"):
                value = read_text(device / "statistics" / name)
                try:
                    values[name] = int(value) if value is not None else None
                except ValueError:
                    values[name] = None
            interfaces[device.name] = values
        return interfaces

    def topology(self):
        cpus = {}
        for directory in sorted((self.sys / "devices/system/cpu").glob("cpu[0-9]*")):
            node = next(iter(directory.glob("node[0-9]*")), None)
            cpus[directory.name] = {"numa_node": int(node.name[4:]) if node else None,
                                   "socket_id": read_text(directory / "topology/physical_package_id"),
                                   "core_id": read_text(directory / "topology/core_id"),
                                   "thread_siblings": read_text(directory / "topology/thread_siblings_list"),
                                   "frequency_khz": read_text(directory / "cpufreq/scaling_cur_freq"),
                                   "governor": read_text(directory / "cpufreq/scaling_governor")}
        networks = []
        for device in sorted((self.sys / "class/net").glob("*")):
            queues = {}
            for queue in sorted((device / "queues").glob("*")):
                queues[queue.name] = {key: read_text(queue / key) for key in ("rps_cpus", "rps_flow_cnt", "xps_cpus") if (queue / key).exists()}
            irq_root = device / "device/msi_irqs"
            irqs = [item.name for item in irq_root.glob("*") if item.name.isdecimal()]
            networks.append({"name": device.name, "numa_node": read_text(device / "device/numa_node"),
                             "queues": queues, "irqs": {irq: {"allowed_cpu_list": read_text(self.proc / "irq" / irq / "smp_affinity_list"),
                                                               "effective_cpu_list": read_text(self.proc / "irq" / irq / "effective_affinity_list")} for irq in irqs}})
        return {"cpus": cpus, "online_cpu_ids": parse_cpu_list(read_text(self.sys / "devices/system/cpu/online")),
                "network_devices": networks, "schedstats_enabled": read_text(self.proc / "sys/kernel/sched_schedstats"),
                "perf_event_paranoid": read_text(self.proc / "sys/kernel/perf_event_paranoid"),
                "soft_affinity": None, "soft_affinity_note": "Product soft-affinity configuration is not inferred from Linux affinity; supply an explicit config artifact."}
