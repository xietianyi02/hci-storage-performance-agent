"""Workflow and tool extension points. Tools must return traceable artifacts."""

from dataclasses import dataclass, field
from typing import Callable

from .models import ExperimentArtifact, FioArtifact, ProfileArtifact


STAGES = [
    ("scope", "定义场景"), ("collect", "采集证据"), ("analyze", "差异分析"),
    ("hypothesize", "根因假设"), ("plan", "验证计划"), ("approve", "专家确认"),
    ("experiment", "实验验证"), ("report", "报告归档"),
]


@dataclass
class WorkflowDefinition:
    id: str
    title: str
    description: str
    focus: list[str]
    tool_roles: dict[str, str] = field(default_factory=lambda: {"fio": "mock_fio", "profile": "mock_profile", "experiment": "mock_experiment"})
    analysis_strategy: str = "thread_placement"
    version: str = "1.0"

    def public(self):
        return {
            **self.__dict__, "tool_ids": list(self.tool_roles.values()), "stages": [{"id": key, "title": title} for key, title in STAGES],
            "mode": "demo", "data_source": "mock", "customizable": True,
        }


class WorkflowRegistry:
    def __init__(self):
        self.items: dict[str, WorkflowDefinition] = {}

    def register(self, definition: WorkflowDefinition):
        if definition.id in self.items:
            raise ValueError(f"Duplicate workflow: {definition.id}")
        self.items[definition.id] = definition

    def get(self, identifier):
        if identifier not in self.items:
            raise ValueError(f"Unknown workflow: {identifier}")
        return self.items[identifier]

    def list(self):
        return [item.public() for item in self.items.values()]


@dataclass
class ToolDefinition:
    id: str
    title: str
    description: str
    execute: Callable[[dict], dict]
    requires_approval: bool = False
    source: str = "mock"
    inputs: list[str] = field(default_factory=lambda: ["case.scenario"])
    output: str = "evidence"
    mode: str = "demo"
    read_only: bool = True
    version: str = "1.0"
    input_model: type | None = None
    output_model: type | None = None

    def public(self):
        return {key: value for key, value in self.__dict__.items() if key not in ("execute", "input_model", "output_model")} | {"status": "available", "input_schema": self.input_model.__name__ if self.input_model else "CaseContext", "output_schema": self.output_model.__name__ if self.output_model else "TraceableArtifact"}


class ToolRegistry:
    def __init__(self):
        self.items: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition):
        if definition.id in self.items:
            raise ValueError(f"Duplicate tool: {definition.id}")
        self.items[definition.id] = definition

    def run(self, identifier: str, case: dict, approved=False):
        tool = self.items[identifier]
        if tool.requires_approval and not approved:
            raise PermissionError(f"Tool {identifier} requires approval")
        if tool.input_model:
            tool.input_model.model_validate(case)
        elif not isinstance(case.get("scenario"), dict) or not isinstance(case["scenario"].get("fio"), dict):
            raise ValueError("Tool requires scenario.fio context")
        result = tool.execute(case)
        if not isinstance(result, dict) or result.get("source") != tool.source:
            raise ValueError(f"Tool {identifier} must return a dict with declared source={tool.source}")
        if tool.output_model:
            result = tool.output_model.model_validate(result).model_dump()
        return result

    def list(self):
        return [item.public() for item in self.items.values()]


@dataclass
class AnalysisStrategy:
    id: str
    title: str
    execute: Callable[[dict], dict]
    version: str = "1.0"


class AnalysisStrategyRegistry:
    def __init__(self):
        self.items: dict[str, AnalysisStrategy] = {}

    def register(self, strategy):
        if strategy.id in self.items:
            raise ValueError(f"Duplicate analysis strategy: {strategy.id}")
        self.items[strategy.id] = strategy

    def run(self, identifier, case):
        result = self.items[identifier].execute(case)
        if not isinstance(result, dict) or not isinstance(result.get("hypotheses"), list) or not isinstance(result.get("recommendations"), list) or not result["hypotheses"] or not result["recommendations"]:
            raise ValueError("Strategy requires hypotheses and recommendations arrays")
        evidence_ids = {item["id"] for item in case["evidence"]}
        for artifact in result["hypotheses"] + result["recommendations"]:
            if not set(artifact.get("evidence_ids", [])).issubset(evidence_ids):
                raise ValueError("Analysis strategy referenced nonexistent evidence")
        for recommendation in result["recommendations"]:
            if recommendation.get("verdict") not in ("pending", "confirmed", "rejected"):
                raise ValueError("Recommendation requires a supported verdict")
        return result


def thread_placement_analysis(case):
    return {
        "hypotheses": [
            {"id": "H1", "title": "tierd 工作线程竞争增加调度等待", "reason": "E2 中模拟调度时延增长；NVMe 忙碌度未饱和，支持优先排查上层等待。", "confidence": 0.76, "confidence_kind": "demo_priority", "evidence_ids": ["E1", "E2"], "status": "candidate"},
            {"id": "H2", "title": "协议处理成为主要瓶颈", "reason": "模拟协议层时延变化较小，当前证据较弱，保留为反证候选。", "confidence": 0.24, "confidence_kind": "demo_priority", "evidence_ids": ["E2"], "status": "candidate"},
        ],
        "recommendations": [
            {"id": "R1", "title": "验证 tierd 工作线程隔离方案", "reason": "优先验证 H1；通过线程调度实验区分 CPU 竞争与设备瓶颈。", "action": "保持 fio 参数、副本布局和版本一致，仅比较一个线程隔离方案。", "expected_effect": "降低调度等待，提升 IOPS；收益需实测确认。", "risk": "低（当前仅模拟；真实线程亲和性变更需环境审核与回滚方案）", "verdict": "pending", "feedback_note": "", "feedback_history": [], "evidence_ids": ["E1", "E2"], "hypothesis_id": "H1", "validation_scope": "demo"},
            {"id": "R2", "title": "采集协议层队列与 CPU 数据", "reason": "H2 的模拟证据不足，需要补充协议层排队与 CPU 利用率。", "action": "后续连接环境后补齐 NFS / vhost / iSCSI 对应采集器。", "expected_effect": "补充反证，判断协议层是否限制吞吐。", "risk": "低（采样开销需先评估）", "verdict": "pending", "feedback_note": "", "feedback_history": [], "evidence_ids": ["E2"], "hypothesis_id": "H2", "validation_scope": "demo"},
        ],
    }


def default_strategies():
    strategies = AnalysisStrategyRegistry()
    strategies.register(AnalysisStrategy("thread_placement", "线程调度候选分析（演示）", thread_placement_analysis))
    return strategies


def metric_values(iops: int, latency: float, block_size: str = "4k"):
    sizes = {"4k": 4096, "8k": 8192, "16k": 16384, "64k": 65536, "128k": 131072, "1m": 1048576}
    return {"iops": iops, "bandwidth_mib": round(iops * sizes[block_size] / 1048576, 1), "latency_ms": latency}


def mock_fio(case):
    workflow = case["workflow_id"]
    baseline, current, lat0, lat1 = {
        "version": (132000, 105600, 0.92, 1.16),
        "poc": (128000, 98000, 0.98, 1.29),
        "research": (158000, 146000, 0.78, 0.85),
    }.get(workflow, (128000, 105600, 0.98, 1.16))
    bs = case["scenario"]["fio"]["bs"]
    fio = case["scenario"]["fio"]
    return {
        "baseline": metric_values(baseline, lat0, bs), "current": metric_values(current, lat1, bs),
        "source": "mock", "repeats": 3, "configured_depth": fio["iodepth"] * fio["numjobs"],
        "achieved_depth": None, "note": "固定演示数据；配置深度不等于实际在途深度；未连接集群或运行 fio。",
    }


def mock_profile(case):
    fio = case["scenario"]["fio"]
    return {
        "layers": [
            {"name": "协议层", "baseline_us": 32, "current_us": 34, "source": "mock"},
            {"name": "IOR / pipeline", "baseline_us": 24, "current_us": 25, "source": "mock"},
            {"name": "route / AFR", "baseline_us": 38, "current_us": 49, "source": "mock"},
            {"name": "tierd 调度", "baseline_us": 48, "current_us": 110, "source": "mock"},
            {"name": "io_uring / NVMe", "baseline_us": 85, "current_us": 88, "source": "mock"},
        ],
        "host_cpu_pct": 62, "storage_thread_cpu_pct": 94, "nvme_busy_pct": 48,
        "remote_rtt_us": 82, "source": "mock", "configured_depth": fio["iodepth"] * fio["numjobs"],
        "note": "模拟独立采样窗口，分层延迟不可直接求和为 fio 总延迟；候选线程热点需真实 perf/trace 证实。",
    }


def mock_experiment(case):
    before = case["metrics"]["current"]
    gain = {"version": 0.205, "poc": 0.269, "research": 0.123}.get(case["workflow_id"], 0.18)
    after = metric_values(round(before["iops"] * (1 + gain)), round(before["latency_ms"] / (1 + gain), 3), case["scenario"]["fio"]["bs"])
    return {"before": before, "after": after, "source": "mock", "repeats": 3,
            "variance_pct": 2.1, "note": "模拟 A/B 结果；未执行硬件调优或真实 fio。"}


def default_registries():
    workflows, tools = WorkflowRegistry(), ToolRegistry()
    workflows.register(WorkflowDefinition("version", "版本内问题排查", "围绕版本基线和变更，建立性能退化的证据链。", ["版本差异", "变更关联", "回归确认"]))
    workflows.register(WorkflowDefinition("poc", "POC 调优", "本地与一个远端数据主机的聚合副本，逐项验证调优收益。", ["环境核对", "瓶颈分层", "调优收益"]))
    workflows.register(WorkflowDefinition("research", "预研性能调优", "控制变量，比较参数与技术方案，沉淀适用条件。", ["假设设计", "参数探索", "实验对比"]))
    tools.register(ToolDefinition("mock_fio", "fio 基线对比", "模拟虚拟机内 fio 指标；预留真实结果导入适配器。", mock_fio, output_model=FioArtifact))
    tools.register(ToolDefinition("mock_profile", "IO 分层剖析", "模拟线程和分层延迟数据；预留 perf / trace 采集适配器。", mock_profile, output_model=ProfileArtifact))
    tools.register(ToolDefinition("mock_experiment", "受控 A/B 实验", "专家确认后生成模拟实验数据，参数与真实环境均不修改。", mock_experiment, True, output="experiment", read_only=False, output_model=ExperimentArtifact))
    return workflows, tools
