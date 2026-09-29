"""Real LangGraph execution with SQLite checkpoints and an explicit approval gate."""

from copy import deepcopy
from pathlib import Path
import sqlite3
from threading import RLock
from typing import TypedDict
from uuid import uuid4

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from .models import CreateCase
from .providers import DemoProvider, configured_provider
from .registry import STAGES, default_registries, default_strategies
from .store import CaseStore, now


class AgentState(TypedDict, total=False):
    case_id: str
    run_id: str
    case: dict
    approval: dict


class AgentService:
    def __init__(self, directory: Path, *, seed=True, workflows=None, tools=None, strategies=None, provider=None):
        self.directory = directory
        self.lock = RLock()
        self.store = CaseStore(directory)
        default_workflows, default_tools = default_registries()
        self.workflows = workflows or default_workflows
        self.tools = tools or default_tools
        self.strategies = strategies or default_strategies()
        self.provider = provider or configured_provider()
        self.checkpoint_connection = sqlite3.connect(directory / "checkpoints.sqlite3", check_same_thread=False)
        self.checkpoint_connection.execute("PRAGMA journal_mode=WAL")
        self.checkpointer = SqliteSaver(self.checkpoint_connection)
        self.checkpointer.setup()
        builder = StateGraph(AgentState)
        for identifier, _ in STAGES:
            builder.add_node(identifier, getattr(self, "_" + identifier))
        builder.add_edge(START, "scope")
        for current, following in zip(STAGES[:5], STAGES[1:6]):
            builder.add_edge(current[0], following[0])
        builder.add_conditional_edges("approve", lambda state: "experiment" if state["approval"]["approved"] else "report")
        builder.add_edge("experiment", "report")
        builder.add_edge("report", END)
        self.graph = builder.compile(checkpointer=self.checkpointer)
        if seed and not self.store.list():
            self._seed()

    @staticmethod
    def _config(run_id):
        return {"configurable": {"thread_id": run_id}}

    def _load(self, state):
        # API edits such as expert feedback must survive checkpoint replay.
        return self.store.get(state["case_id"])

    def _workflow_snapshot(self, case):
        return case["runs"][-1].get("workflow_snapshot") or self.workflows.get(case["workflow_id"]).public()

    def _save_stage(self, case, identifier, summary, *, status="completed", source="system"):
        for stage in case["stages"]:
            if stage["id"] == identifier:
                stage.update(status=status, summary=summary, finished_at=now() if status == "completed" else None)
        self.store.event(case, identifier, dict(STAGES)[identifier], summary,
                         source=source, dedupe_key=f"{case['run_id']}:{identifier}:{status}")
        self.store.save(case)
        return {"case": case}

    def create_case(self, payload: CreateCase, *, case_id=None, is_seed=False):
        self.workflows.get(payload.workflow_id)
        case = {
            "id": case_id or "HCI-" + uuid4().hex[:8].upper(),
            "title": payload.title.strip(), "workflow_id": payload.workflow_id,
            "workflow_title": self.workflows.get(payload.workflow_id).title,
            "protocol": payload.protocol, "description": payload.description,
            "owner": payload.owner, "status": "new", "created_at": now(), "updated_at": now(),
            "baseline_version": payload.baseline_version, "target_version": payload.target_version,
            "scenario": {"layout": "poc_aggregated", "layout_label": "POC 聚合副本",
                         "nodes": 3, "local_host": "10.174.188.68", "remote_host": "10.174.188.66",
                         "data_hosts": 2, "business_entry": "IOR", "fio": payload.fio.model_dump()},
            "stages": [{"id": key, "title": title, "status": "pending", "summary": "待执行"} for key, title in STAGES],
            "evidence": [], "hypotheses": [], "recommendations": [], "experiments": [],
            "metrics": None, "report": None, "runs": [], "run_id": None,
            "mode": "demo", "data_source": "mock", "is_seed": is_seed,
            "summary": payload.description or "等待创建标准化分析与验证流程。",
        }
        with self.lock:
            self.store.save(case)
            self.store.event(case, "scope", "案例创建", "案例已保存，指标与实验均使用模拟数据。", source="system")
        return self.store.get(case["id"])

    def run(self, case_id):
        with self.lock:
            case = self.store.get(case_id)
            if case["status"] == "awaiting_approval":
                raise ValueError("当前运行正在等待专家确认，请确认或取消模拟实验。")
            if case["status"] == "running":
                raise ValueError("案例已有运行正在执行。")
            run_id = "RUN-" + uuid4().hex
            case.update(status="running", run_id=run_id, metrics=None, report=None,
                        evidence=[], hypotheses=[], recommendations=[], experiments=[], approval=None,
                        analysis=None, knowledge_ids=[])
            case["stages"] = [{"id": key, "title": title, "status": "pending", "summary": "待执行"} for key, title in STAGES]
            workflow = self.workflows.get(case["workflow_id"])
            case["runs"].append({"id": run_id, "status": "running", "started_at": now(), "workflow_version": workflow.version,
                                 "workflow_snapshot": workflow.public(), "tool_snapshot": [self.tools.items[key].public() for key in workflow.tool_roles.values()],
                                 "strategy_version": self.strategies.items[workflow.analysis_strategy].version, "graph_version": "1.0", "data_source": "mock"})
            self.store.save(case)
            self.store.event(case, "scope", "运行开始", f"LangGraph 已启动运行 {run_id}，将在实验执行前暂停。", kind="run")
            try:
                self.graph.invoke({"case_id": case_id, "run_id": run_id, "case": case}, self._config(run_id))
            except Exception as exc:
                self._failed(case_id, exc)
                raise
            return self.store.get(case_id)

    def approve(self, case_id, approval):
        with self.lock:
            case = self.store.get(case_id)
            if case["status"] != "awaiting_approval":
                raise ValueError("案例不处于等待确认状态；不能重复执行实验。")
            if not self.graph.get_state(self._config(case["run_id"])).next:
                raise ValueError("未找到待恢复的 LangGraph checkpoint。")
            try:
                self.graph.invoke(Command(resume=approval), self._config(case["run_id"]))
            except Exception as exc:
                self._failed(case_id, exc)
                raise
            return self.store.get(case_id)

    def _failed(self, case_id, exc):
        case = self.store.get(case_id)
        case["status"] = "failed"
        case["summary"] = "执行失败，已保留过程与证据。"
        if case["runs"]:
            case["runs"][-1].update(status="failed", finished_at=now())
        self.store.event(case, "report", "运行失败", f"{type(exc).__name__}: 运行未完成，请检查服务配置。", kind="error")
        self.store.save(case)

    def _scope(self, state):
        case = self._load(state)
        workflow = self.workflows.get(case["workflow_id"])
        case["knowledge_ids"] = ["architecture", "scope", "nfs_path", "method"]
        case["summary"] = "场景已定义；正按协议层、共用业务层与本地/远端数据路径组织分析。"
        depth = case["scenario"]["fio"]["iodepth"] * case["scenario"]["fio"]["numjobs"]
        return self._save_stage(case, "scope", f"{workflow.title} · {case['protocol'].upper()} · 本地 + 一个远端 · 配置深度 {depth}（非实测在途深度）")

    def _collect(self, state):
        case = self._load(state)
        roles = self._workflow_snapshot(case)["tool_roles"]
        metrics = self.tools.run(roles["fio"], case)
        profile = self.tools.run(roles["profile"], case)
        case["metrics"] = metrics
        case["evidence"] = [
            {"id": "E1", "title": "虚拟机 fio 基线与当前指标", "summary": "三次模拟样本，同一 fio 模型用于基线对比。", "source": "mock", "type": "fio", "stage_id": "collect", "data": metrics},
            {"id": "E2", "title": "IO 分层时延与线程热点", "summary": "tierd 调度延迟模拟升高，设备模拟忙碌度未饱和。", "source": "mock", "type": "profile", "stage_id": "collect", "data": profile},
            {"id": "E3", "title": "场景与配置快照", "summary": "三节点集群，仅本地与一个远端保存数据；IOR 为业务入口。", "source": "mock", "type": "configuration", "stage_id": "collect", "data": deepcopy(case["scenario"])},
        ]
        if case["workflow_id"] == "version":
            case["evidence"].append({"id": "E4", "title": "版本变更关联样例", "summary": "演示线索：线程调度参数发生变化；仅相关性，尚不构成因果证据。", "source": "mock", "type": "change", "stage_id": "collect", "data": {"baseline_version": case["baseline_version"], "target_version": case["target_version"], "component": "tierd", "changed": "线程调度参数（虚构变更样例）"}})
        for tool_id in (roles["fio"], roles["profile"]):
            self.store.event(case, "collect", "工具调用 · " + tool_id, "读取固定模拟样本；未访问真实集群。", kind="tool", source="mock", dedupe_key=f"{case['run_id']}:tool:{tool_id}")
        return self._save_stage(case, "collect", f"已归档 {len(case['evidence'])} 份模拟证据，来源、参数与工具调用均可追溯。", source="mock")

    def _analyze(self, state):
        case = self._load(state)
        baseline = case["metrics"]["baseline"]
        current = case["metrics"]["current"]
        diff = {"iops": round((current["iops"] / baseline["iops"] - 1) * 100, 1),
                "latency_ms": round((current["latency_ms"] / baseline["latency_ms"] - 1) * 100, 1)}
        provider = DemoProvider() if case["is_seed"] else self.provider
        case["analysis"] = {"delta_pct": diff, "provider": provider.name, "explanation": provider.explain(case, case["evidence"]), "source": "mock"}
        return self._save_stage(case, "analyze", f"模拟 IOPS 变化 {diff['iops']:+.1f}%，时延变化 {diff['latency_ms']:+.1f}%；优先检查 tierd 调度等待。", source="mock")

    def _hypothesize(self, state):
        case = self._load(state)
        result = self.strategies.run(self._workflow_snapshot(case)["analysis_strategy"], case)
        case.update(result)
        return self._save_stage(case, "hypothesize", f"形成 {len(case['hypotheses'])} 个候选假设与 {len(case['recommendations'])} 条建议；置信度仅表示演示排序，不表示真实诊断准确率。", source="mock")

    def _plan(self, state):
        case = self._load(state)
        case["experiments"] = [{
            "id": "EXP-" + case["run_id"][4:12], "title": "线程隔离单变量 A/B 验证", "status": "planned", "source": "mock",
            "before": case["metrics"]["current"], "after": None, "change_pct": None, "recommendation_ids": [case["recommendations"][0]["id"]],
            "procedure": ["固定 fio 模型与两台数据主机", "记录方案前的指标与分层等待", "仅改变一个线程隔离因素（模拟）", "进行 3 次方案后采样（模拟）", "比较 IOPS、时延与样本波动", "专家判定建议是否有效"],
            "rollback": "真实接入后恢复原线程配置并重跑基线；当前演示不改变配置。",
            "acceptance": "IOPS 改善超过样本波动且时延不恶化，并由专家确认。",
        }]
        result = self._save_stage(case, "plan", "单变量验证计划已生成；模拟实验前等待专家确认。", source="mock")
        case["status"] = "awaiting_approval"
        case["runs"][-1]["status"] = "awaiting_approval"
        case["summary"] = "已完成差异分析与验证计划，等待专家确认模拟实验。"
        self._save_stage(case, "approve", "等待确认：执行模拟 A/B 实验，实际环境不会修改。", status="blocked")
        result["case"] = case
        return result

    def _approve(self, state):
        # No side effect before interrupt: this node restarts during resume.
        approval = interrupt({"kind": "experiment_approval", "case_id": state["case_id"], "run_id": state["run_id"], "title": "确认执行模拟 A/B 实验", "source": "mock"})
        if not isinstance(approval, dict) or not isinstance(approval.get("approved"), bool):
            raise ValueError("Approval must contain a boolean approved field")
        case = self._load(state)
        case["approval"] = approval | {"timestamp": now()}
        case["status"] = "running" if approval["approved"] else "cancelled"
        self.store.event(case, "approve", "专家确认" if approval["approved"] else "专家取消实验", approval.get("note", ""), kind="approval", source="expert", dedupe_key=f"{case['run_id']}:decision")
        if not approval["approved"]:
            for experiment in case["experiments"]:
                experiment["status"] = "cancelled"
            for stage in case["stages"]:
                if stage["id"] == "experiment":
                    stage.update(status="skipped", summary="专家取消，未执行实验。")
        return self._save_stage(case, "approve", "已确认模拟实验。" if approval["approved"] else "已取消模拟实验。") | {"approval": approval}

    def _experiment(self, state):
        case = self._load(state)
        experiment = case["experiments"][0]
        if experiment["status"] != "completed":
            tool_id = self._workflow_snapshot(case)["tool_roles"]["experiment"]
            result = self.tools.run(tool_id, case, approved=state["approval"]["approved"])
            before, after = result["before"], result["after"]
            experiment.update(result, status="completed", finished_at=now(), change_pct={
                "iops": round((after["iops"] / before["iops"] - 1) * 100, 1),
                "latency_ms": round((after["latency_ms"] / before["latency_ms"] - 1) * 100, 1),
            })
            case["metrics"]["after"] = after
            case["hypotheses"][0]["status"] = "supported"
            case["evidence"].append({"id": "E5", "title": "模拟实验前后指标", "summary": "A/B 模拟数据支持继续验证线程隔离方案；不构成真实根因证明。", "source": "mock", "type": "experiment", "stage_id": "experiment", "data": result})
            # Persist the tool result before checkpoint completion; replay reuses it.
            self.store.save(case)
            self.store.event(case, "experiment", "工具调用 · " + tool_id, "已生成 3 次模拟 A/B 采样；结果归档并可重复读取。", kind="tool", source=result["source"], dedupe_key=f"{case['run_id']}:tool:{tool_id}")
        return self._save_stage(case, "experiment", f"模拟 IOPS 改善 {experiment['change_pct']['iops']:+.1f}%；建议仍待专家评价，不自动判定正确。", source="mock")

    def _report(self, state):
        case = self._load(state)
        cancelled = case["status"] == "cancelled"
        case["status"] = "cancelled" if cancelled else "completed"
        case["summary"] = "实验已取消；过程、建议与证据已归档。" if cancelled else "模拟分析与验证已完成；请评价建议有效性，作为运营统计依据。"
        case["report"] = {
            "summary": case["summary"], "conclusion": "尚未执行实验，根因保持候选状态。" if cancelled else "模拟实验支持优先验证 tierd 调度方向。真实根因与调优效果需连接环境后重新验证。",
            "limitations": ["指标、剖析与实验全部为 MOCK 数据。", "未连接真实集群、版本编译或升级工具。", "vhost 与 iSCSI 的 IOR 前链路待补充，未推断其内部实现。", "建议评价属于演示范围，不代表真实诊断准确率。"],
            "source": "mock", "knowledge_candidate": True,
        }
        case["runs"][-1].update(status=case["status"], finished_at=now())
        return self._save_stage(case, "report", "报告已归档，证据与反馈保留；已确认的建议可进入案例知识库。", source="mock")

    def feedback(self, case_id, payload):
        with self.lock:
            current = self.store.get(case_id)
            historical = bool(payload.run_id and payload.run_id != current["run_id"])
            case = self.store.get_run(case_id, payload.run_id) if historical else current
            recommendation = next((item for item in case["recommendations"] if item["id"] == payload.recommendation_id), None)
            if recommendation is None:
                raise KeyError("recommendation")
            if payload.verdict == "confirmed" and not any(
                item["status"] == "completed" and recommendation["id"] in item["recommendation_ids"] for item in case["experiments"]
            ):
                raise ValueError("确认有效需要关联已完成实验；当前建议尚无对应验证结果。")
            feedback = {"verdict": payload.verdict, "note": payload.note, "reviewer": payload.reviewer, "timestamp": now(), "source": "mock" if case["is_seed"] else "expert", "validation_scope": "demo"}
            recommendation.update(verdict=payload.verdict, feedback_note=payload.note, reviewed_at=feedback["timestamp"], reviewer=payload.reviewer)
            recommendation["feedback_history"].append(feedback)
            self.store.event(case, "report", "建议评价 · " + recommendation["id"], f"{payload.reviewer}: {payload.verdict}。{payload.note}", kind="feedback", source=feedback["source"], data={"recommendation_id": recommendation["id"], **feedback})
            if historical:
                self.store.save_historical_run(case)
                return self.store.get_run(case_id, payload.run_id)
            self.store.save(case)
            return self.store.get(case_id)

    def overview(self):
        cases = self.store.list()
        runs = self.store.all_runs()
        recommendations = [item for run in runs for item in run["recommendations"]]
        counts = {key: sum(item["verdict"] == key for item in recommendations) for key in ("confirmed", "rejected", "pending")}
        evaluated = counts["confirmed"] + counts["rejected"]
        stats = {
            "total_cases": len(cases), "completed_cases": sum(case["status"] == "completed" for case in cases),
            "awaiting_approval": sum(case["status"] == "awaiting_approval" for case in cases),
            "total_runs": len(runs), "completed_runs": sum(run["status"] == "completed" for run in runs),
            "recommendations": {"total": len(recommendations), **counts, "evaluated": evaluated,
                                "accuracy_pct": round(counts["confirmed"] / evaluated * 100, 1) if evaluated else None,
                                "validation_scope": "demo", "denominator": "confirmed + rejected; pending excluded"},
        }
        return stats | {"stats": stats, "cases": len(cases), "completed": stats["completed_cases"], "mode": "demo", "data_source": "mock",
                        "by_workflow": [{"id": item["id"], "title": item["title"], "count": sum(case["workflow_id"] == item["id"] for case in cases), "completed": sum(case["workflow_id"] == item["id"] and case["status"] == "completed" for case in cases)} for item in self.workflows.list()],
                        "recent_cases": cases[:5]}

    def _seed(self):
        from .models import FeedbackInput
        version = self.create_case(CreateCase(title="V6.1 高深度随机写性能退化", workflow_id="version", description="模拟版本案例：同 fio 模型下 IOPS 下降 20%，追溯差异、验证候选并评价建议。"), case_id="HCI-2609-001", is_seed=True)
        self.run(version["id"])
        self.approve(version["id"], {"approved": True, "note": "演示种子案例：确认模拟实验。", "reviewer": "示例专家"})
        self.feedback(version["id"], FeedbackInput(recommendation_id="R1", verdict="confirmed", note="示例评价：模拟结果支持该方向，真实环境仍待验证。", reviewer="示例专家"))
        self.feedback(version["id"], FeedbackInput(recommendation_id="R2", verdict="rejected", note="示例评价：当前模拟证据不支持协议层为主要瓶颈。", reviewer="示例专家"))
        poc = self.create_case(CreateCase(title="客户 POC · NFS 4K 随机写调优", workflow_id="poc", description="模拟 POC：两台数据主机的聚合副本，优先验证线程调度因素。"), case_id="HCI-2609-002", is_seed=True)
        self.run(poc["id"])
        self.create_case(CreateCase(title="vhost 低深度时延预研", workflow_id="research", protocol="vhost", fio={"iodepth": 1, "numjobs": 1, "rw": "randread"}, description="预研待办：以低深度模型设计控制变量实验；协议层采集适配器待补充。"), case_id="HCI-2609-003", is_seed=True)

    def close(self):
        self.checkpoint_connection.close()
        self.store.close()
