"""Campaign-local parameter experiments, repeatability and expert feedback."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from statistics import median
from threading import RLock
from typing import TypedDict
from uuid import uuid4

from langgraph.graph import END, START, StateGraph

from .poc_models import ParameterDefinition, PocCampaignInput, PocFeedback, PocTrialInput
from .poc_registry import canonical, normalized_definition, validate_configuration
from .poc_seed import demo_campaign, demo_trials, example_parameters
from .poc_store import PocStore
from .store import now


METRICS = ("iops", "bandwidth_mib_s", "latency_ms", "p99_ms", "disk_latency_ms", "tierd_depth")
STEPS = (("validate", "核对固定实验范围"), ("group_trials", "按参数配置归组"),
         ("compare_candidates", "比较重复样本与约束"), ("report", "记录候选与验证入口"))


def group_id(parameters):
    return "PG-" + sha256(canonical(parameters).encode()).hexdigest()[:16]


class PocState(TypedDict):
    campaign: dict
    analysis: dict


class PocService:
    def __init__(self, directory: Path, *, seed=True):
        self.lock = RLock()
        self.store = PocStore(Path(directory))
        builder = StateGraph(PocState)
        for identifier, _ in STEPS:
            builder.add_node(identifier, getattr(self, "_" + identifier))
        builder.add_edge(START, "validate")
        for (before, _), (after, _) in zip(STEPS, STEPS[1:]):
            builder.add_edge(before, after)
        builder.add_edge("report", END)
        self.graph = builder.compile()
        if seed:
            known = {definition["id"] for definition in self.parameters()}
            for definition in example_parameters():
                if definition["id"] not in known:
                    self.register_parameter(ParameterDefinition.model_validate(definition))
            if not self.store.campaigns():
                campaign = self.create_campaign(PocCampaignInput.model_validate(demo_campaign()))
                for trial in demo_trials(campaign):
                    self.add_trial(campaign["id"], PocTrialInput.model_validate(trial))
                self.analyze(campaign["id"])

    def parameters(self):
        return self.store.parameters()

    def register_parameter(self, payload: ParameterDefinition):
        with self.lock:
            return self.store.register_parameter(normalized_definition(payload))

    def campaigns(self):
        with self.lock:
            return [self.detail(campaign["id"]) for campaign in self.store.campaigns()]

    def create_campaign(self, payload: PocCampaignInput):
        model = payload if isinstance(payload, PocCampaignInput) else PocCampaignInput.model_validate(payload)
        value = model.model_dump(mode="json")
        with self.lock:
            registered = {definition["id"]: definition for definition in self.parameters()}
            if set(value["baseline_parameters"]) - set(registered):
                raise ValueError("基线包含未注册参数；先注册真实定义及适用范围。")
            definitions = [deepcopy(registered[identifier]) for identifier in sorted(value["baseline_parameters"])]
            hardware = value["environment"]["hardware"]
            if "data_hosts" in hardware and (type(hardware["data_hosts"]) is not int or hardware["data_hosts"] != 2):
                raise ValueError("此 POC 场景只支持本地和一台远端，共两台数据主机。")
            validate_configuration(definitions, value["baseline_parameters"], value["protocol"], hardware, value["fio"])
            value["id"] = value["id"] or "PC-" + uuid4().hex
            value.update(created_at=now(), parameter_definitions=definitions)
            self.store.create_campaign(value)
            return self.detail(value["id"])

    def detail(self, identifier):
        with self.lock:
            campaign = self.store.campaign(identifier)
            campaign["trials"] = self.store.trials(identifier)
            history = self.store.analyses(identifier)
            campaign["analysis_history"] = history
            # A new imported trial invalidates the current projection while old
            # analyses and all expert judgments remain accessible in history.
            campaign["analysis"] = history[-1] if history and history[-1]["trial_count"] == len(campaign["trials"]) else None
            return campaign

    def add_trial(self, identifier, payload: PocTrialInput):
        model = payload if isinstance(payload, PocTrialInput) else PocTrialInput.model_validate(payload)
        value = model.model_dump(mode="json")
        with self.lock:
            campaign = self.store.campaign(identifier)
            fixed = {"environment_id": campaign["environment"]["id"], "build": campaign["build"],
                     "protocol": campaign["protocol"], "source": campaign["source"], "fio": campaign["fio"]}
            if any(canonical(value[key]) != canonical(expected) for key, expected in fixed.items()):
                raise ValueError("测试轮次必须保持同一环境、版本、协议、完整 fio 模型及 mock/measured 来源。")
            validate_configuration(campaign["parameter_definitions"], value["parameters"], campaign["protocol"],
                                   campaign["environment"]["hardware"], campaign["fio"])
            value["id"] = value["id"] or "PT-" + uuid4().hex
            value["created_at"] = now()
            self.store.add_trial(identifier, value)
            return self.detail(identifier)

    def analyze(self, identifier):
        with self.lock:
            campaign = self.detail(identifier)
            analysis = {"id": "PA-" + uuid4().hex, "created_at": now(), "framework": "LangGraph",
                        "trial_count": len(campaign["trials"]), "source": campaign["source"],
                        "steps": [{"id": key, "title": title, "status": "pending", "detail": ""} for key, title in STEPS],
                        "groups": [], "baseline_group_id": group_id(campaign["baseline_parameters"]),
                        "best_observed_group_id": None, "recommendation": None, "correlations": [], "summary": ""}
            result = self.graph.invoke({"campaign": campaign, "analysis": analysis})["analysis"]
            self.store.add_analysis(identifier, len(campaign["trials"]), result)
            return self.detail(identifier)

    @staticmethod
    def _finish(analysis, identifier, detail):
        for step in analysis["steps"]:
            if step["id"] == identifier:
                step.update(status="completed", detail=detail)
        return {"analysis": analysis}

    def _validate(self, state):
        campaign, analysis = state["campaign"], deepcopy(state["analysis"])
        validate_configuration(campaign["parameter_definitions"], campaign["baseline_parameters"], campaign["protocol"],
                               campaign["environment"]["hardware"], campaign["fio"])
        return self._finish(analysis, "validate", "已锁定本实验的设备档案、版本、协议、fio 与来源；结果不跨实验推荐。POC 数据布局为本地及一台远端。")

    def _group_trials(self, state):
        campaign, analysis = state["campaign"], deepcopy(state["analysis"])
        grouped = {}
        for trial in campaign["trials"]:
            key = canonical(trial["parameters"])
            grouped.setdefault(key, []).append(trial)
        for key, trials in grouped.items():
            medians, ranges = {}, {}
            for metric in METRICS:
                values = [trial["metrics"][metric] for trial in trials if trial["metrics"][metric] is not None]
                # Optional metrics with a missing observation stay unknown as a
                # complete-group median; min/max also expose known coverage.
                medians[metric] = median(values) if len(values) == len(trials) else None
                ranges[metric] = {"min": min(values) if values else None, "max": max(values) if values else None,
                                  "known": len(values), "total": len(trials)}
            reasons = []
            if len(trials) < campaign["goal"]["min_repeats"]:
                reasons.append(f"重复 {len(trials)} 次，少于要求 {campaign['goal']['min_repeats']} 次")
            limit = campaign["goal"]["p99_limit_ms"]
            if limit is not None:
                values = [trial["metrics"]["p99_ms"] for trial in trials]
                if any(value is None for value in values):
                    reasons.append("部分 p99 未知，不能核对时延上限")
                elif any(value > limit for value in values):
                    reasons.append(f"部分样本 p99 超过 {limit} ms 上限")
            parameters = trials[0]["parameters"]
            analysis["groups"].append({"id": group_id(parameters), "parameters": parameters,
                                       "trial_ids": [trial["id"] for trial in trials], "repeats": len(trials),
                                       "is_baseline": key == canonical(campaign["baseline_parameters"]),
                                       "eligible": not reasons, "reason": "；".join(reasons) or "重复数量与当前已设置约束满足",
                                       "medians": medians, "range": ranges})
        analysis["groups"].sort(key=lambda group: (not group["is_baseline"], canonical(group["parameters"])))
        return self._finish(analysis, "group_trials", f"按完整参数配置归为 {len(analysis['groups'])} 组；保留各指标中位数、范围及缺失覆盖。")

    def _compare_candidates(self, state):
        campaign, analysis = state["campaign"], deepcopy(state["analysis"])
        baseline = next((group for group in analysis["groups"] if group["is_baseline"]), None)
        if baseline is None or baseline["repeats"] < campaign["goal"]["min_repeats"]:
            return self._finish(analysis, "compare_candidates", "基线重复样本不足，不生成参数推荐。")
        if not any(not group["is_baseline"] and group["repeats"] >= campaign["goal"]["min_repeats"] for group in analysis["groups"]):
            return self._finish(analysis, "compare_candidates", "缺少重复数量足够的其他配置对照，不生成调优推荐。")
        eligible = [group for group in analysis["groups"] if group["eligible"]]
        if not eligible:
            return self._finish(analysis, "compare_candidates", "没有满足重复次数和时延约束的配置，不生成参数推荐。")
        metric = campaign["goal"]["metric"]
        reverse = metric != "latency_ms"
        eligible.sort(key=lambda group: ((-1 if reverse else 1) * group["medians"][metric], not group["is_baseline"], group["id"]))
        best = eligible[0]
        before, after = baseline["medians"][metric], best["medians"][metric]
        analysis["best_observed_group_id"] = best["id"]
        scope_note = "只代表本实验当前样本目标指标最佳候选，需复测；不适用于其他设备、版本或 fio 模型。"
        if campaign["goal"]["p99_limit_ms"] is None:
            scope_note += "未设置时延上限。"
        analysis["recommendation"] = {"group_id": best["id"], "parameters": best["parameters"],
                                      "goal_metric": metric, "baseline_value": before, "observed_value": after,
                                      "change_pct": round((after - before) * 100 / before, 4) if before else None,
                                      "verdict": "pending", "note": "", "review_history": [], "scope_note": scope_note,
                                      "keep_baseline": best["is_baseline"]}
        return self._finish(analysis, "compare_candidates", "满足约束的历史样本中按目标中位数排序；候选待专家验证。" + scope_note)

    @staticmethod
    def _correlations(campaign, groups):
        results = []
        for definition in campaign["parameter_definitions"]:
            if definition["value_type"] not in ("integer", "number"):
                continue
            parameter = definition["id"]
            strata = {}
            for group in groups:
                if group["repeats"] < campaign["goal"]["min_repeats"]:
                    continue
                fixed = {key: value for key, value in group["parameters"].items() if key != parameter}
                strata.setdefault(canonical(fixed), []).append(group)
            for fixed, rows in strata.items():
                if len({canonical(row["parameters"][parameter]) for row in rows}) < 2:
                    continue
                observables = [name for name in definition["observables"] if name in METRICS and all(row["medians"][name] is not None for row in rows)]
                if not observables:
                    continue
                rows.sort(key=lambda row: row["parameters"][parameter])
                points = [{"value": row["parameters"][parameter], "group_id": row["id"],
                           "medians": {name: row["medians"][name] for name in observables}} for row in rows]
                results.append({"parameter_id": parameter, "observables": observables, "fixed_parameters": {key: value for key, value in rows[0]["parameters"].items() if key != parameter},
                                "points": points, "causal": False,
                                "summary": f"其他参数固定为 {fixed} 时，{definition['label']} 的 {len(points)} 个值与 {', '.join(observables)} 存在可对照观测；仅记录关联，不证明因果或越大越好。"})
        return results

    def _report(self, state):
        campaign, analysis = state["campaign"], deepcopy(state["analysis"])
        analysis["correlations"] = self._correlations(campaign, analysis["groups"])
        if analysis["recommendation"] is None:
            summary = "当前没有可推荐参数配置；补足同环境、同版本、同 fio 的基线及候选重复测试，核对已设置的 p99 约束。"
        else:
            action = "保持基线" if analysis["recommendation"]["keep_baseline"] else "验证候选配置"
            summary = f"建议{action}。当前样本目标指标最佳候选仍待专家验证，不能据此宣称全局最优。"
            if campaign["goal"]["p99_limit_ms"] is None:
                summary += "未设置时延上限，需复测并补充约束。"
        if campaign["source"] == "mock":
            summary += "所有数据为模拟，未调整真实设备。"
        analysis["summary"] = summary
        return self._finish(analysis, "report", "本次分析与规则步骤已追加保存；专家反馈独立追加，历史结论不会覆盖。")

    def feedback(self, identifier, payload: PocFeedback):
        model = payload if isinstance(payload, PocFeedback) else PocFeedback.model_validate(payload)
        with self.lock:
            history = self.store.analyses(identifier)
            self.store.campaign(identifier)
            analysis = next((item for item in history if item["id"] == model.analysis_id), None)
            if analysis is None:
                raise ValueError("分析 ID 不属于本实验；不能评价其他实验的候选。")
            if analysis["recommendation"] is None:
                raise ValueError("该分析没有参数候选，不能填写确认结论。")
            self.store.feedback(analysis["id"], {"verdict": model.verdict, "note": model.note,
                                                 "reviewer": model.reviewer, "timestamp": now()})
            return self.detail(identifier)

    def close(self):
        self.store.close()


# Compatibility for callers using the acronym form in the initial design.
POCService = PocService
