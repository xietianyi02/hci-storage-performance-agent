"""Behavioral tests for the durable approval and expert-feedback workflow."""

from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.graph import AgentService
from backend.main import create_app
from backend.models import CreateCase


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as api:
        yield api


def make_case(client, workflow="poc", protocol="nfs"):
    response = client.post("/api/cases", json={"title": "测试场景", "workflow_id": workflow, "protocol": protocol})
    assert response.status_code == 201
    return response.json()["id"]


@pytest.mark.parametrize("workflow,protocol", [("version", "nfs"), ("poc", "iscsi"), ("research", "vhost")])
def test_run_pauses_then_approval_completes(client, workflow, protocol):
    case_id = make_case(client, workflow, protocol)
    case = client.post(f"/api/cases/{case_id}/run").json()
    assert case["status"] == "awaiting_approval"
    assert case["run_id"] != case["id"]
    assert case["experiments"][0]["status"] == "planned"
    assert case["experiments"][0]["after"] is None
    assert case["scenario"]["data_hosts"] == 2
    assert case["scenario"]["business_entry"] == "IOR"
    assert case["metrics"]["achieved_depth"] is None
    result = client.post(f"/api/cases/{case_id}/approve", json={"approved": True}).json()
    assert result["status"] == "completed"
    assert result["experiments"][0]["after"]["iops"] > result["experiments"][0]["before"]["iops"]
    assert all(item["source"] == "mock" for item in result["evidence"])
    assert all(item["verdict"] == "pending" for item in result["recommendations"])
    assert client.post(f"/api/cases/{case_id}/approve", json={"approved": True}).status_code == 409
    tools = [event for event in result["events"] if event["title"] == "工具调用 · mock_experiment"]
    assert len(tools) == 1


def test_restart_resumes_exact_checkpoint_and_keeps_feedback(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as client:
        case_id = make_case(client)
        case = client.post(f"/api/cases/{case_id}/run").json()
        run_id = case["run_id"]
        assert client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "R2", "verdict": "rejected", "note": "证据不足", "reviewer": "张工"}).status_code == 200
    with TestClient(create_app(tmp_path, seed=False)) as client:
        loaded = client.get(f"/api/cases/{case_id}").json()
        assert loaded["status"] == "awaiting_approval"
        assert loaded["run_id"] == run_id
        response = client.post(f"/api/cases/{case_id}/approve", json={"approved": True})
        assert response.status_code == 200
        case = response.json()
        assert case["status"] == "completed"
        assert case["recommendations"][1]["verdict"] == "rejected"
        assert case["recommendations"][1]["feedback_history"][0]["reviewer"] == "张工"
        assert len([item for item in case["events"] if item["kind"] == "feedback"]) == 1
        assert len([item for item in case["events"] if item["title"] == "工具调用 · mock_fio"]) == 1


def test_cancel_does_not_execute_experiment(client):
    case_id = make_case(client)
    client.post(f"/api/cases/{case_id}/run")
    case = client.post(f"/api/cases/{case_id}/approve", json={"approved": False, "note": "先补充环境信息"}).json()
    assert case["status"] == "cancelled"
    assert case["experiments"][0]["status"] == "cancelled"
    assert next(stage for stage in case["stages"] if stage["id"] == "experiment")["status"] == "skipped"
    assert not any(item["title"] == "工具调用 · mock_experiment" for item in case["events"])


def test_feedback_requires_experiment_and_correctness_denominator(client):
    case_id = make_case(client)
    client.post(f"/api/cases/{case_id}/run")
    assert client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "R1", "verdict": "confirmed"}).status_code == 409
    client.post(f"/api/cases/{case_id}/approve", json={"approved": True})
    client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "R1", "verdict": "confirmed", "note": "演示实验有效"})
    stats = client.get("/api/overview").json()["recommendations"]
    assert stats["confirmed"] == 1 and stats["pending"] == 1
    assert stats["accuracy_pct"] == 100 and stats["evaluated"] == 1
    client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "R2", "verdict": "rejected", "note": "无法支持"})
    stats = client.get("/api/overview").json()["recommendations"]
    assert stats["accuracy_pct"] == 50 and stats["evaluated"] == 2
    assert client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "missing", "verdict": "rejected"}).status_code == 404


def test_seed_is_repeatable_and_awaiting_case_has_checkpoint(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert len(client.get("/api/cases").json()) == 3
        assert client.get("/api/health").json()["framework"] == "LangGraph"
    with TestClient(create_app(tmp_path)) as client:
        assert len(client.get("/api/cases").json()) == 3
        assert client.post("/api/cases/HCI-2609-002/approve", json={"approved": True}).json()["status"] == "completed"


def test_unknown_workflow_and_api_not_spa(client):
    assert client.post("/api/cases", json={"title": "测试", "workflow_id": "unknown"}).status_code == 409
    assert client.get("/api/missing").status_code == 404
    assert client.get("/api/cases/missing").status_code == 404


def test_tool_result_survives_checkpoint_replay(tmp_path):
    service = AgentService(tmp_path, seed=False)
    try:
        case = service.create_case(CreateCase(title="重放测试"))
        service.run(case["id"])
        service.approve(case["id"], {"approved": True})
        count = 0
        original = service.tools.items["mock_experiment"].execute

        def tracking(case):
            nonlocal count
            count += 1
            return original(case)

        service.tools.items["mock_experiment"].execute = tracking
        loaded = service.store.get(case["id"])
        service._experiment({"case_id": case["id"], "run_id": loaded["run_id"], "approval": {"approved": True}})
        assert count == 0
        assert len([event for event in service.store.get(case["id"])["events"] if event["title"] == "工具调用 · mock_experiment"]) == 1
    finally:
        service.close()


def test_rerun_retains_each_runs_evidence_reviews_and_stats(client):
    case_id = make_case(client)
    client.post(f"/api/cases/{case_id}/run")
    first = client.post(f"/api/cases/{case_id}/approve", json={"approved": True}).json()
    first_id = first["run_id"]
    client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "R1", "verdict": "confirmed", "note": "第一次验证有效"})
    second = client.post(f"/api/cases/{case_id}/run").json()
    assert second["run_id"] != first_id
    history = client.get(f"/api/cases/{case_id}/runs").json()
    assert len(history) == 2
    retained = client.get(f"/api/cases/{case_id}/runs/{first_id}").json()
    assert retained["status"] == "completed"
    assert retained["recommendations"][0]["verdict"] == "confirmed"
    assert retained["recommendations"][0]["feedback_note"] == "第一次验证有效"
    assert len(retained["evidence"]) == len(first["evidence"])
    assert retained["experiments"][0]["status"] == "completed"
    assert all(event["run_id"] == first_id for event in retained["events"])
    stats = client.get("/api/overview").json()["recommendations"]
    assert stats == {"total": 4, "confirmed": 1, "rejected": 0, "pending": 3, "evaluated": 1, "accuracy_pct": 100, "validation_scope": "demo", "denominator": "confirmed + rejected; pending excluded"}
    assert any(item.get("case_id") == case_id for item in client.get("/api/knowledge").json())
    updated = client.post(f"/api/cases/{case_id}/feedback", json={"recommendation_id": "R1", "run_id": first_id, "verdict": "rejected", "note": "历史复核"}).json()
    assert updated["run_id"] == first_id
    assert client.get(f"/api/cases/{case_id}").json()["recommendations"][0]["verdict"] == "pending"
    assert client.get("/api/overview").json()["recommendations"]["rejected"] == 1


def test_registered_workflow_tools_and_strategy_drive_execution(tmp_path):
    from backend.registry import AnalysisStrategy, ToolDefinition, WorkflowDefinition, default_registries, default_strategies, mock_fio, thread_placement_analysis
    workflows, tools = default_registries()
    strategies = default_strategies()
    calls = []

    def custom_collector(case):
        calls.append("custom_collector")
        return mock_fio(case)

    def custom_strategy(case):
        calls.append("custom_strategy")
        result = thread_placement_analysis(case)
        result["recommendations"][0]["title"] = "自定义调优策略"
        return result

    tools.register(ToolDefinition("custom_fio", "自定义 fio 采集器", "测试插件", custom_collector))
    strategies.register(AnalysisStrategy("custom", "自定义分析", custom_strategy, version="2.0"))
    workflows.register(WorkflowDefinition("custom", "扩展流程", "自定义工作流", ["扩展验证"], tool_roles={"fio": "custom_fio", "profile": "mock_profile", "experiment": "mock_experiment"}, analysis_strategy="custom", version="2.0"))
    service = AgentService(tmp_path, seed=False, workflows=workflows, tools=tools, strategies=strategies)
    try:
        case = service.create_case(CreateCase(title="插件验证", workflow_id="custom"))
        result = service.run(case["id"])
        assert calls == ["custom_collector", "custom_strategy"]
        assert result["recommendations"][0]["title"] == "自定义调优策略"
        assert result["runs"][-1]["workflow_version"] == "2.0"
        assert result["runs"][-1]["tool_snapshot"][0]["id"] == "custom_fio"
        assert service.approve(case["id"], {"approved": True})["status"] == "completed"
        tools.register(ToolDefinition("invalid", "错误输出", "验证输出来源", lambda case: {"source": "live"}))
        with pytest.raises(ValueError, match="declared source"):
            tools.run("invalid", result)
    finally:
        service.close()
