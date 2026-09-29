"""Version-diagnosis rules: independent callables, evidence-based candidates."""

from dataclasses import dataclass
from typing import Callable

from .version_metrics import percent_change, stable_key


def candidate(identifier, kind, title, priority, evidence, missing, validation):
    return {"id": identifier, "kind": kind, "title": title, "priority": priority, "evidence": evidence,
            "missing": missing, "validation": validation, "verdict": "pending", "note": "", "review_history": []}


def paired_threads(comparison):
    return [row for row in comparison["threads"] if row["before"] and row["after"]]


def scope_id(row):
    return stable_key({"scope": row["key"]})[:8]


def cpu_cost_rule(comparison):
    results = []
    for row in paired_threads(comparison):
        previous, current = row["derived_before"], row["derived_after"]
        delta = percent_change(current["cpu_us_per_io"], previous["cpu_us_per_io"])
        cpu_rate_delta = percent_change(current["cpu_pct"], previous["cpu_pct"])
        if delta is not None and delta >= 15 and cpu_rate_delta is not None and cpu_rate_delta >= 10:
            results.append(candidate("CPU-" + scope_id(row), "cpu_cost", f"{row['after']['pool']} 每 IO CPU 成本上升", "medium",
                [f"{row['key']}：{previous['cpu_us_per_io']:.3f} → {current['cpu_us_per_io']:.3f} μs/IO（{delta:+.1f}%）。", f"线程池 CPU 消耗速率：{previous['cpu_pct']:.1f}% → {current['cpu_pct']:.1f}%（{cpu_rate_delta:+.1f}%）。", "分母使用各自同窗口 guest 完成数；仅吞吐下降引起的成本增幅保留在指标表，不作为 CPU 根因候选。"],
                ["CPU 成本上升不单独证明该线程池是根因。"], ["同模型至少重复三轮，核对吞吐波动和线程池 CPU 时间。", "用同窗口调用栈确认新增执行或忙等路径。", "固定环境后只改变一个相关因素做 A/B 验证。 "]))
    return results


def scheduling_rule(comparison):
    results = []
    for row in paired_threads(comparison):
        before, after = row["before"], row["after"]
        duration_before = comparison["previous"]["window"]["duration_s"]
        duration_after = comparison["current"]["window"]["duration_s"]
        wait_before = before["runqueue_wait_ms"] / duration_before if before["runqueue_wait_ms"] is not None else None
        wait_after = after["runqueue_wait_ms"] / duration_after if after["runqueue_wait_ms"] is not None else None
        delta = percent_change(wait_after, wait_before)
        if delta is None or delta < 50 or after["runqueue_wait_ms"] < 1000:
            continue
        busy = [cpu for cpu in comparison["current"]["cpus"] if cpu["host_role"] == after["host_role"] and cpu["cpu_id"] in after["cpu_ids"] and cpu["idle_pct"] <= 15]
        evidence = [f"{row['key']} 调度等待总量 {before['runqueue_wait_ms']:.1f} → {after['runqueue_wait_ms']:.1f} ms，采样 {duration_before} → {duration_after} s；等待速率 {wait_before:.3f} → {wait_after:.3f} ms/s（{delta:+.1f}%）。"]
        evidence.extend(f"逻辑 CPU {cpu['cpu_id']} idle={cpu['idle_pct']:.1f}%；同 CPU 竞争任务：{', '.join(cpu['competitors']) or '未标注'}。" for cpu in busy)
        results.append(candidate("SCHED-" + scope_id(row), "scheduling", f"{after['pool']} 调度等待与 CPU 竞争", "high" if busy else "medium", evidence,
            [] if busy else ["等待升高，仍需同 CPU 的竞争与调度时序证据。"], ["采集同窗口调度等待、CPU 放置与竞争任务。", "保持 fio 与副本不变，比较线程与竞争任务隔离前后。", "恢复原放置并复测，确认变化能重复。 "]))
    return results


def numa_rule(comparison):
    results = []
    for row in paired_threads(comparison):
        before, after = row["before"], row["after"]
        different_nodes = bool(after["numa_nodes"] and after["memory_numa_nodes"] and set(after["numa_nodes"]).isdisjoint(after["memory_numa_nodes"]))
        placement_changed = before["numa_nodes"] != after["numa_nodes"] or before["memory_numa_nodes"] != after["memory_numa_nodes"] or before["cpu_ids"] != after["cpu_ids"]
        remote_before, remote_after = before["remote_access_pct"], after["remote_access_pct"]
        increased = remote_after is not None and remote_before is not None and remote_after - remote_before >= 10
        if not (different_nodes and placement_changed) and not increased:
            continue
        evidence = [f"{row['key']} 执行 NUMA 节点 {after['numa_nodes']}，内存节点 {after['memory_numa_nodes']}。"]
        if remote_after is not None:
            evidence.append(f"远端访存占比：{remote_before if remote_before is not None else '缺失'} → {remote_after}%。")
        results.append(candidate("NUMA-" + scope_id(row), "numa", f"{after['pool']} NUMA 放置需核对", "medium", evidence,
            ["线程与内存节点不同仅是放置线索，不等于已证明远端访问造成退化。"] if remote_after is None else ["仍需访问时延或内存带宽证据建立因果。"],
            ["核对线程实际执行 CPU、内存页 NUMA 分布与远端访存来源。", "保持吞吐模型一致，单独验证内存与线程本地化。 "]))
    return results


def ipc_rule(comparison):
    results = []
    for row in paired_threads(comparison):
        before, after = row["before"], row["after"]
        if before["counter_scope"] != after["counter_scope"]:
            continue
        previous, current = row["derived_before"], row["derived_after"]
        delta = percent_change(current["ipc"], previous["ipc"])
        if delta is not None and delta <= -15:
            results.append(candidate("IPC-" + scope_id(row), "execution_efficiency", f"{after['pool']} 指令执行效率下降", "medium",
                [f"线程池总 instructions/cycles：IPC {previous['ipc']:.3f} → {current['ipc']:.3f}（{delta:+.1f}%）。", f"计数范围：{after['counter_scope']}；覆盖比例 {after['counting_ratio']}。"],
                ["IPC 下降不能单独区分缓存、访存、分支或流水线停顿。"], ["对比同范围、充分计数的 PMU 停顿与缓存事件。", "结合每 IO 指令数、cycle 数和调用栈，避免只看 IPC。 "]))
    return results


def network_rule(comparison):
    results = []
    for row in comparison["network"]:
        before, after = row["before"], row["after"]
        if not before or not after:
            continue
        duration_before = comparison["previous"]["window"]["duration_s"]
        duration_after = comparison["current"]["window"]["duration_s"]
        increased = [field for field in ("drops", "retransmits") if before[field] is not None and after[field] is not None and after[field] / duration_after > before[field] / duration_before and after[field] > 0]
        bound = [cpu for cpu in comparison["cpus"] if cpu["after"] and cpu["before"] and cpu["after"]["host_role"] == after["host_role"] and cpu["after"]["cpu_id"] in after["cpu_ids"]]
        softirq = [cpu for cpu in bound if cpu["after"]["softirq_pct"] - cpu["before"]["softirq_pct"] >= 10]
        irq_delta = percent_change(after["irq_per_s"], before["irq_per_s"])
        if not increased and not (softirq and irq_delta is not None and irq_delta >= 20):
            continue
        display = lambda value: "缺失" if value is None else f"{value:,.1f}"
        evidence = [f"{row['key']} IRQ/s：{display(before['irq_per_s'])} → {display(after['irq_per_s'])}；包/s：{display(before['packets_per_s'])} → {display(after['packets_per_s'])}。"]
        evidence.extend(f"{field} 同窗口计数 {before[field]} → {after[field]}；采样秒数 {duration_before} → {duration_after}。" for field in increased)
        evidence.extend(f"队列绑定逻辑 CPU {cpu['after']['cpu_id']} 的整体 softirq：{cpu['before']['softirq_pct']}% → {cpu['after']['softirq_pct']}%（不能视为队列独占耗时）。" for cpu in softirq)
        missing = []
        if not bound:
            missing.append("缺少绑定 CPU 的两轮 IRQ / softirq 对照，不能核对处理压力。")
        if after["packets_per_s"] is None:
            missing.append("每队列包速率缺失；不能将接口总量当作该队列数据。")
        missing.append("丢包、重传与 softirq 变化是相关证据，仍需验证因果。")
        host_label = {"guest": "虚拟机", "local": "本地", "remote": "远端"}[after["host_role"]]
        results.append(candidate("NET-" + scope_id(row), "network", f"{host_label} {after['interface']} 网络处理异常", "high" if softirq and increased else "medium", evidence, missing,
            ["对齐本地与远端的网卡队列、IRQ CPU、丢包与 TCP 重传窗口。", "核对 IRQ / RPS / RSS 放置与竞争 CPU；仅验证一个网络处理因素。", "同 fio 模型重复对照，观察丢包/重传、softirq 与性能是否一起恢复。 "]))
    return results


@dataclass
class VersionRule:
    id: str
    stage: str
    evaluate: Callable[[dict], list[dict]]
    version: str = "1.0"


class VersionRuleRegistry:
    def __init__(self):
        self.rules: dict[str, VersionRule] = {}

    def register(self, rule):
        if rule.id in self.rules:
            raise ValueError(f"Duplicate version rule: {rule.id}")
        if rule.stage not in ("cpu", "network"):
            raise ValueError("Rule stage must be cpu or network")
        self.rules[rule.id] = rule

    def run(self, stage, comparison):
        results = []
        for rule in self.rules.values():
            if rule.stage != stage:
                continue
            emitted = rule.evaluate(comparison)
            if not isinstance(emitted, list):
                raise ValueError("Rule must emit a candidate list")
            for item in emitted:
                if not item.get("evidence") or item.get("priority") not in ("high", "medium") or item.get("verdict") != "pending":
                    raise ValueError("Rule candidates require evidence, priority and pending verdict")
            results.extend(emitted)
        if len({item["id"] for item in results}) != len(results):
            raise ValueError("Rule candidate IDs must be unique")
        return results

    def versions(self):
        return [{"id": rule.id, "stage": rule.stage, "version": rule.version} for rule in self.rules.values()]


def default_version_rules():
    registry = VersionRuleRegistry()
    for identifier, stage, evaluate in [("cpu_cost", "cpu", cpu_cost_rule), ("scheduling", "cpu", scheduling_rule), ("numa", "cpu", numa_rule), ("ipc", "cpu", ipc_rule), ("network", "network", network_rule)]:
        registry.register(VersionRule(identifier, stage, evaluate, version="1.1" if identifier == "cpu_cost" else "1.0"))
    return registry
