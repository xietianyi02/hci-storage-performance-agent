"""Offline version diagnosis driven by immutable, normalized test evidence."""

from copy import deepcopy
from pathlib import Path
import sqlite3
from threading import RLock
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from .store import now
from .version_metrics import derive_thread, percent_change, stable_key
from .version_models import Batch, VersionFeedback
from .version_seed import import_example, seed_batches
from .version_rules import default_version_rules
from .version_store import VersionStore


STEPS = [("pair", "确认可比批次"), ("fio", "识别性能变化"), ("cpu", "核对 CPU 成本与放置"),
         ("network", "核对网络处理"), ("candidates", "生成证据候选"), ("archive", "归档分析与评价入口")]


def fio_key(snapshot):
    return stable_key({"protocol": snapshot["protocol"], "fio": snapshot["fio"]})


def thread_key(row):
    return "/".join(str(row[key]) for key in ("host_role", "process_role", "pool", "branch"))


def cpu_key(row):
    return f"{row['host_role']}/{row['cpu_id']}"


def network_key(row):
    return f"{row['host_role']}/{row['interface']}/{row['queue']}"


def matched_rows(previous, current, category, key_function):
    before = {key_function(row): row for row in (previous or {}).get(category, [])}
    after = {key_function(row): row for row in current.get(category, [])}
    return [{"key": key, "before": before.get(key), "after": after.get(key)} for key in sorted(before.keys() | after.keys())]


class VersionState(TypedDict):
    comparison_id: str


class VersionService:
    def __init__(self, directory: Path, *, seed=True, rules=None):
        self.lock = RLock()
        self.store = VersionStore(directory)
        self.rules = rules or default_version_rules()
        self.checkpoint_connection = sqlite3.connect(directory / "version_checkpoints.sqlite3", check_same_thread=False)
        self.checkpointer = SqliteSaver(self.checkpoint_connection)
        self.checkpointer.setup()
        builder = StateGraph(VersionState)
        for identifier, _ in STEPS:
            builder.add_node(identifier, getattr(self, "_" + identifier))
        builder.add_edge(START, "pair")
        for before, after in zip(STEPS, STEPS[1:]):
            builder.add_edge(before[0], after[0])
        builder.add_edge("archive", END)
        self.graph = builder.compile(checkpointer=self.checkpointer)
        if seed and not self.store.catalog():
            for batch in seed_batches():
                self.import_batch(Batch.model_validate(batch))

    def import_batch(self, payload: Batch):
        batch = payload.model_dump(mode="json")
        with self.lock:
            return self.store.import_batch(batch)

    def batches(self):
        return self.store.list_batches()

    def catalog(self):
        return self.store.catalog()

    @staticmethod
    def _pair_id(batch, current, previous_batch, previous):
        return "VC-" + stable_key({"current_batch": batch["id"], "current_scenario": current["id"], "previous_batch": previous_batch["id"] if previous_batch else None, "previous_scenario": previous["id"] if previous else None, "source": batch["source"], "environment": batch["environment"]["id"], "model": fio_key(current)})[:24]

    @staticmethod
    def _batch_summary(batch):
        return {key: batch[key] for key in ("id", "title", "build", "created_at", "changes")} if batch else None

    @staticmethod
    def _deltas(current, previous):
        return {"iops_pct": percent_change(current["metrics"]["iops"], previous["metrics"]["iops"]) if previous else None,
                "latency_pct": percent_change(current["metrics"]["latency_ms"], previous["metrics"]["latency_ms"]) if previous else None,
                "p99_pct": percent_change(current["metrics"]["p99_ms"], previous["metrics"]["p99_ms"]) if previous else None}

    def scenario_summaries(self, batch_id):
        with self.lock:
            summaries = []
            for sample in self.store.scenario_summaries(batch_id):
                batch, current, previous_batch, previous = self.store.pair_metadata(batch_id, sample["id"])
                summaries.append({"id": self._pair_id(batch, current, previous_batch, previous),
                                  "current_batch": self._batch_summary(batch), "previous_batch": self._batch_summary(previous_batch),
                                  "current": current, "previous": previous, "deltas": self._deltas(current, previous)})
            return summaries

    @staticmethod
    def import_example():
        return Batch.model_validate(import_example()).model_dump(mode="json")

    def comparison(self, batch_id, scenario_id, include_samples=True):
        with self.lock:
            batch, current, previous_batch, previous = self.store.pair_metadata(batch_id, scenario_id)
            identifier = self._pair_id(batch, current, previous_batch, previous)
            try:
                return self.store.get_comparison(identifier, include_samples=include_samples)
            except KeyError:
                pass
            current = self.store.get_snapshot(batch_id, scenario_id)
            previous = self.store.get_snapshot(previous_batch["id"], previous["id"]) if previous else None
            threads = matched_rows(previous, current, "threads", thread_key)
            for row in threads:
                row["derived_before"] = derive_thread(row["before"], previous["window"]) if previous else None
                row["derived_after"] = derive_thread(row["after"], current["window"])
            quality = self._quality(current, previous, threads)
            comparison = {"id": identifier, "source": batch["source"], "environment": batch["environment"],
                          "current_batch": self._batch_summary(batch), "previous_batch": self._batch_summary(previous_batch), "current": current, "previous": previous,
                          "deltas": self._deltas(current, previous),
                          "threads": threads, "cpus": matched_rows(previous, current, "cpus", cpu_key),
                          "network": matched_rows(previous, current, "network", network_key), "quality": quality, "analysis": None}
            self.store.save_comparison(comparison)
            return comparison if include_samples else self.store.get_comparison(identifier, include_samples=False)

    @staticmethod
    def _quality(current, previous, threads):
        quality = list(current["quality"])
        if previous:
            quality.extend(previous["quality"])
        else:
            quality.append("首次记录：未找到同来源、同环境、同协议且完整 fio 相同的更早批次；没有基线。")
        for label, sample in (("本次", current), ("上次", previous)):
            if sample is None:
                continue
            windows = sample.get("sample_windows", [])
            inventory = {(row["host_role"], row["cpu_id"]) for row in sample.get("cpu_inventory", [])}
            if not inventory:
                quality.append(f"{label}未提供 CPU 采集清单，全量逻辑 CPU 范围未知；已采集 CPU 不能代表其余 CPU 空闲。")
            elif any(inventory - {(row["host_role"], row["cpu_id"]) for row in window["cpus"]} for window in windows):
                quality.append(f"{label}部分分窗 CPU 数据缺失；清单中的缺失 CPU 保持未知，不填为 0%。")
            if not windows:
                quality.append(f"{label}没有分窗时序数据；整轮线程池摘要不能还原每个 3 秒窗口的线程落核、CPU 或 IPC。")
            else:
                if any(row["iops"] is None for row in windows):
                    quality.append(f"{label}部分采样窗口缺少 guest 完成 IO 计数，IOPS 保持未知；不使用整轮平均值补点。")
                if any(thread["instructions"] is None or thread["cycles"] is None or thread["counting_ratio"] is None or thread["counting_ratio"] < .9 for window in windows for thread in window["threads"]):
                    quality.append(f"{label}部分单线程分窗 PMU 计数缺失或覆盖低于 90%，这些窗口的 IPC 不可使用。")
                if not any(window["layers"] for window in windows):
                    quality.append(f"{label}各层时延与深度尚未采集，扩展字段保留为空。")
            if sample["window"]["completed_ios"] is None or sample["window"]["completed_ios"] == 0:
                quality.append(f"{label}同窗口 guest 完成数缺失或为 0，每 IO 指标不可计算。")
            for category, name in (("threads", "线程"), ("cpus", "逻辑 CPU"), ("network", "网卡")):
                if not sample[category]:
                    quality.append(f"{label}{name}数据缺失，相关候选无法验证。")
        for row in threads:
            for label, capture in (("本次", row["after"]), ("上次", row["before"])):
                if not capture:
                    quality.append(f"{row['key']} 在{label}缺失，不能直接归因于消耗变化。")
                elif capture["instructions"] is None or capture["cycles"] is None or capture["counting_ratio"] is None or capture["counting_ratio"] < .9:
                    quality.append(f"{label} {row['key']} PMU 计数缺失或计数覆盖低于 90%，IPC 不可直接比较。")
            if row["before"] and row["after"] and row["before"]["counter_scope"] != row["after"]["counter_scope"]:
                quality.append(f"{row['key']} 上次与本次计数范围不同，不能据 IPC 变化推断执行效率。")
        return list(dict.fromkeys(quality))

    def analyze(self, batch_id, scenario_id, include_samples=True):
        with self.lock:
            comparison = self.comparison(batch_id, scenario_id)
            if comparison["analysis"] and all(step["status"] == "completed" for step in comparison["analysis"]["steps"]):
                return comparison if include_samples else self.store.get_comparison(comparison["id"], include_samples=False)
            if comparison["analysis"] is None:
                comparison["analysis"] = {"id": "VA-" + uuid4().hex, "created_at": now(), "framework": "LangGraph", "steps": [{"id": identifier, "title": title, "status": "pending"} for identifier, title in STEPS], "summary": "", "candidates": [], "events": [], "rule_versions": self.rules.versions()}
                self.store.save_comparison(comparison)
            configuration = {"configurable": {"thread_id": comparison["analysis"]["id"]}}
            state = self.graph.get_state(configuration)
            # Retry the saved next node after an interrupted process; don't append
            # another analysis or discard expert feedback on the same pairing.
            self.graph.invoke(None if state.next else {"comparison_id": comparison["id"]}, configuration)
            return self.store.get_comparison(comparison["id"], include_samples=include_samples)

    def _finish(self, comparison, stage_id, detail):
        for step in comparison["analysis"]["steps"]:
            if step["id"] == stage_id:
                step["status"] = "completed"
        self.store.event(comparison["id"], stage_id, dict(STEPS)[stage_id], detail, comparison["source"])
        self.store.save_comparison(comparison)
        return {}

    def _pair(self, state):
        comparison = self.store.get_comparison(state["comparison_id"])
        detail = "已锁定同来源、同环境、同协议及完整 fio 的时间上最近上轮。" if comparison["previous"] else "首次记录，不生成假基线或性能下降结论。"
        return self._finish(comparison, "pair", detail)

    @staticmethod
    def _regressed(comparison):
        deltas = comparison["deltas"]
        return (deltas["iops_pct"] is not None and deltas["iops_pct"] <= -5) or (deltas["latency_pct"] is not None and deltas["latency_pct"] >= 10)

    def _fio(self, state):
        comparison = self.store.get_comparison(state["comparison_id"])
        detail = "没有可比上轮。" if comparison["previous"] is None else f"IOPS 变化 {comparison['deltas']['iops_pct']}%，平均时延变化 {comparison['deltas']['latency_pct']}%。阈值仅用于筛查，未排除样本波动。"
        return self._finish(comparison, "fio", detail)

    def _cpu(self, state):
        comparison = self.store.get_comparison(state["comparison_id"])
        candidates = self.rules.run("cpu", comparison) if self._regressed(comparison) else []
        comparison["analysis"]["candidates"] = candidates
        return self._finish(comparison, "cpu", f"按线程池角色匹配，计算 CPU 时间 / 同窗口完成 IO、池总 instructions / cycles；生成 {len(candidates)} 个 CPU 相关候选。")

    def _network(self, state):
        comparison = self.store.get_comparison(state["comparison_id"])
        candidates = self.rules.run("network", comparison) if self._regressed(comparison) else []
        existing_ids = {item["id"] for item in comparison["analysis"]["candidates"]}
        if any(item["id"] in existing_ids for item in candidates):
            raise ValueError("Candidate IDs collided across rule stages")
        comparison["analysis"]["candidates"].extend(candidates)
        return self._finish(comparison, "network", f"按主机、接口与队列匹配 IRQ、包速率、丢包与重传；生成 {len(candidates)} 个网络候选，未把 CPU softirq 归为队列独占时间。")

    def _candidates(self, state):
        comparison = self.store.get_comparison(state["comparison_id"])
        comparison["analysis"]["candidates"].sort(key=lambda candidate: (candidate["priority"] != "high", candidate["kind"] == "cpu_cost", candidate["id"]))
        if comparison["previous"] is None:
            summary = "首次记录：保存本轮证据，尚无可比上轮，不能判断版本退化。"
        elif not self._regressed(comparison):
            summary = "未发现超过筛查阈值的 IOPS 下降或平均时延上升；不生成固定根因建议，仍需核对重复样本波动。"
        elif not comparison["analysis"]["candidates"]:
            summary = "检测到性能变化，但现有线程、CPU 与网络证据不足以生成具体根因候选；先补齐同窗口采集。"
        else:
            summary = f"检测到性能变化，形成 {len(comparison['analysis']['candidates'])} 个证据候选。所有候选待验证，当前没有自动确认的根因。"
        comparison["analysis"]["summary"] = summary
        return self._finish(comparison, "candidates", summary)

    def _archive(self, state):
        comparison = self.store.get_comparison(state["comparison_id"])
        return self._finish(comparison, "archive", "不可变配对、原始引用、指标口径和候选已保存；重复分析复用本记录，专家评价追加留存。")

    def feedback(self, batch_id, scenario_id, payload: VersionFeedback, include_samples=True):
        with self.lock:
            comparison = self.comparison(batch_id, scenario_id)
            analysis = comparison["analysis"]
            if analysis is None or analysis["id"] != payload.analysis_id:
                raise ValueError("分析 ID 不属于当前配对；请刷新后评价对应分析。")
            if not all(step["status"] == "completed" for step in analysis["steps"]):
                raise ValueError("分析尚未完成，请等待完整候选后评价。")
            candidate = next((item for item in analysis["candidates"] if item["id"] == payload.candidate_id), None)
            if candidate is None:
                raise KeyError(payload.candidate_id)
            review = {"verdict": payload.verdict, "note": payload.note, "reviewer": payload.reviewer, "timestamp": now()}
            candidate.update(verdict=payload.verdict, note=payload.note)
            candidate["review_history"].append(review)
            self.store.append_feedback(comparison["id"], candidate["id"], review)
            self.store.event(comparison["id"], "feedback", "专家评价 · " + candidate["title"], f"{payload.reviewer}: {payload.verdict}。{payload.note}", comparison["source"], data={"analysis_id": analysis["id"], "candidate_id": candidate["id"], **review})
            self.store.save_comparison(comparison)
            return self.store.get_comparison(comparison["id"], include_samples=include_samples)

    def close(self):
        self.checkpoint_connection.close()
        self.store.close()
