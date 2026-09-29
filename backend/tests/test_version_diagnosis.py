"""Version evidence pairing, arithmetic, registry execution and durable reviews."""

from copy import deepcopy
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app
from backend.version_metrics import derive_thread, weighted_ipc
from backend.version_models import Batch
from backend.version_rules import VersionRule, VersionRuleRegistry, candidate
from backend.version_seed import seed_batches
from backend.version_service import VersionService


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as api:
        yield api


def import_batch(client, batch):
    result = client.post("/api/version/batches", json=batch)
    assert result.status_code == 201, result.text
    return result.json()


def seeds(client):
    batches = seed_batches()
    for batch in batches:
        import_batch(client, batch)
    return batches


def url(batch="VB-DEMO-29", scenario="nfs-high-write"):
    return f"/api/version/comparisons/{batch}/{scenario}"


def test_previous_uses_same_full_fio_not_scenario_id_and_nearest_time(client):
    batches = seed_batches()
    batches[1]["scenarios"][0]["id"] = "previous-renamed"
    for batch in batches:
        import_batch(client, batch)
    result = client.get(url(scenario="nfs-low-read")).json()
    assert result["previous_batch"]["id"] == "VB-DEMO-28"
    assert result["previous"]["id"] == "previous-renamed"
    changed = deepcopy(batches[-1])
    changed["id"] = "VB-EXTRA-FIO"
    changed["created_at"] = "2026-09-30T02:00:00+00:00"
    changed["scenarios"][0]["fio"]["rate_iops"] = 40000
    import_batch(client, changed)
    result = client.get(url(changed["id"], "nfs-low-read")).json()
    assert result["previous"] is None
    assert all(delta is None for delta in result["deltas"].values())


@pytest.mark.parametrize("dimension", ["source", "environment", "protocol"])
def test_source_environment_and_protocol_isolated(client, dimension):
    before, _, after = seed_batches()
    if dimension == "source":
        after["source"] = "measured"
    elif dimension == "environment":
        after["environment"]["id"] = "another-environment"
    else:
        after["scenarios"][3]["protocol"] = "vhost"
    import_batch(client, before)
    import_batch(client, after)
    result = client.post(url() + "/analyze").json()
    assert result["previous"] is None
    assert result["analysis"]["candidates"] == []
    assert "首次记录" in result["analysis"]["summary"]


def test_cpu_and_nic_candidates_follow_values_and_stable_has_no_fixed_root(client):
    seeds(client)
    cpu_result = client.post(url(scenario="nfs-low-read") + "/analyze").json()
    cpu_kinds = {item["kind"] for item in cpu_result["analysis"]["candidates"]}
    assert {"scheduling", "numa", "execution_efficiency"}.issubset(cpu_kinds)
    nic_result = client.post(url() + "/analyze").json()
    assert len(nic_result["analysis"]["candidates"]) == 2
    assert {item["kind"] for item in nic_result["analysis"]["candidates"]} == {"network"}
    assert nic_result["analysis"]["candidates"][0]["kind"] == "network"
    assert nic_result["analysis"]["candidates"][0]["priority"] == "high"
    assert {item["title"].split()[0] for item in nic_result["analysis"]["candidates"]} == {"本地", "远端"}
    assert not any("00000000003" in evidence for item in nic_result["analysis"]["candidates"] for evidence in item["evidence"])
    assert all(item["verdict"] == "pending" for item in nic_result["analysis"]["candidates"])
    assert nic_result["analysis"]["framework"] == "LangGraph"
    assert all(step["status"] == "completed" for step in nic_result["analysis"]["steps"])
    assert len(nic_result["analysis"]["events"]) == 6
    stable = client.post(url(scenario="nfs-high-read") + "/analyze").json()
    assert stable["analysis"]["candidates"] == []
    assert "未发现" in stable["analysis"]["summary"]


def test_derived_cost_window_denominator_and_total_counter_ipc():
    assert weighted_ipc([{"instructions": 10, "cycles": 10}, {"instructions": 9, "cycles": 90}]) == pytest.approx(.19)
    capture = deepcopy(seed_batches()[0]["scenarios"][0]["threads"][0])
    capture.update(cpu_time_ms=4000, instructions=19, cycles=100, thread_count=2)
    derived = derive_thread(capture, {"duration_s": 10, "completed_ios": 2000})
    assert derived["cpu_pct"] == 40
    assert derived["cpu_us_per_io"] == 2000
    assert derived["ipc"] == pytest.approx(.19)
    assert derived["instructions_per_io"] == pytest.approx(19 / 2000)
    assert derive_thread(capture, {"duration_s": 10, "completed_ios": None})["cpu_us_per_io"] is None
    assert derive_thread(capture, {"duration_s": 10, "completed_ios": 0})["cycles_per_io"] is None
    capture["counting_ratio"] = .4
    assert derive_thread(capture, {"duration_s": 10, "completed_ios": 2000})["ipc"] is None


def test_missing_cpu_and_pmu_data_remains_unknown_no_guessed_numa(client):
    before, _, after = seed_batches()
    for batch in (before, after):
        sample = batch["scenarios"][0]
        sample["window"]["completed_ios"] = None
        for row in sample["threads"]:
            row.update(cpu_time_ms=None, instructions=None, cycles=None, runqueue_wait_ms=None,
                       numa_nodes=[], memory_numa_nodes=[], remote_access_pct=None, counting_ratio=None)
        for row in sample["cpus"]:
            row["numa_node"] = None
        sample["network"] = []
        import_batch(client, batch)
    result = client.post(url(scenario="nfs-low-read") + "/analyze").json()
    assert result["analysis"]["candidates"] == []
    assert all(row["derived_after"]["cpu_us_per_io"] is None and row["derived_after"]["ipc"] is None for row in result["threads"])
    assert any("完成数缺失" in message for message in result["quality"])
    assert "不足" in result["analysis"]["summary"]


def test_scope_mismatch_blocks_ipc_candidate(client):
    before, _, after = seed_batches()
    after["scenarios"][0]["threads"][1]["counter_scope"] = "different_scope"
    import_batch(client, before)
    import_batch(client, after)
    result = client.post(url(scenario="nfs-low-read") + "/analyze").json()
    assert "execution_efficiency" not in {item["kind"] for item in result["analysis"]["candidates"]}
    assert any("范围不同" in message for message in result["quality"])


def test_review_persists_and_analyze_is_idempotent_after_restart(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as client:
        seeds(client)
        result = client.post(url() + "/analyze").json()
        analysis_id = result["analysis"]["id"]
        candidate_id = result["analysis"]["candidates"][0]["id"]
        review = {"analysis_id": analysis_id, "candidate_id": candidate_id, "verdict": "confirmed", "reviewer": "张工", "note": "真实验证尚未进行；此为模拟评价"}
        assert client.post(url() + "/feedback", json=review).status_code == 200
        review["verdict"] = "pending"
        review["note"] = "复核后仍待补充证据"
        assert client.post(url() + "/feedback", json=review).status_code == 200
    with TestClient(create_app(tmp_path, seed=False)) as client:
        repeated = client.post(url() + "/analyze").json()
        assert repeated["analysis"]["id"] == analysis_id
        selected = next(item for item in repeated["analysis"]["candidates"] if item["id"] == candidate_id)
        assert selected["verdict"] == "pending" and len(selected["review_history"]) == 2
        assert len(repeated["analysis"]["events"]) == 8
        review["analysis_id"] = "stale-analysis"
        assert client.post(url() + "/feedback", json=review).status_code == 409


def test_late_chronological_import_creates_new_pair_keeps_old_review(client):
    before, middle, after = seed_batches()
    import_batch(client, before)
    import_batch(client, after)
    original = client.post(url() + "/analyze").json()
    assert original["previous_batch"]["id"] == before["id"]
    review = {"analysis_id": original["analysis"]["id"], "candidate_id": original["analysis"]["candidates"][0]["id"], "verdict": "rejected", "note": "旧配对评价", "reviewer": "示例专家"}
    client.post(url() + "/feedback", json=review)
    import_batch(client, middle)
    refreshed = client.get(url()).json()
    assert refreshed["id"] != original["id"] and refreshed["analysis"] is None
    assert refreshed["previous_batch"]["id"] == middle["id"]
    assert client.post(url() + "/feedback", json=review).status_code == 409
    retained = client.app.state.version_service.store.get_comparison(original["id"])
    assert retained["analysis"]["candidates"][0]["verdict"] == "rejected"


def test_import_validates_immutable_environment_and_route_safe_ids(client):
    batch = seed_batches()[0]
    import_batch(client, batch)
    assert client.post("/api/version/batches", json=batch).status_code == 409
    changed = deepcopy(batch)
    changed["id"] = "VB-OTHER"
    changed["environment"]["description"] = "different hardware"
    assert client.post("/api/version/batches", json=changed).status_code == 409
    changed = deepcopy(batch)
    changed["id"] = "VB-UNREACHABLE/ID"
    assert client.post("/api/version/batches", json=changed).status_code == 422
    changed["id"] = "VB-INVALID-CPU"
    changed["scenarios"][0]["threads"][0]["cpu_ids"] = [-1]
    assert client.post("/api/version/batches", json=changed).status_code == 422
    example = client.get("/api/version/import-example").json()
    assert example["source"] == "mock"
    assert client.post("/api/version/batches", json=example).status_code == 201


def test_custom_version_rule_is_executed_and_version_recorded(tmp_path):
    rules = VersionRuleRegistry()
    observed = []

    def custom(comparison):
        observed.append(comparison["id"])
        return [candidate("CUSTOM", "custom", "扩展分析规则", "medium", ["已对齐同模型采集"], ["待验证"], ["执行后续实验"])]

    rules.register(VersionRule("custom-rule", "cpu", custom, version="3.0"))
    service = VersionService(tmp_path, rules=rules)
    try:
        result = service.analyze("VB-DEMO-29", "nfs-low-read")
        assert len(observed) == 1
        assert result["analysis"]["candidates"][0]["id"] == "CUSTOM"
        assert result["analysis"]["rule_versions"] == [{"id": "custom-rule", "stage": "cpu", "version": "3.0"}]
        assert service.analyze("VB-DEMO-29", "nfs-low-read")["analysis"]["id"] == result["analysis"]["id"]
        assert len(observed) == 1
    finally:
        service.close()


def test_cpu_cost_and_wait_compare_aligned_rates_not_raw_window_totals(client):
    before, _, after = seed_batches()
    sample_before, sample_after = before["scenarios"][0], after["scenarios"][0]
    # Four times as long a sampling window should not turn unchanged CPU/wait
    # rates into a scheduling regression or inflate per-IO CPU cost.
    sample_after["window"]["duration_s"] = 240
    # This test exercises legacy whole-window rate normalization; its synthetic
    # four-minute window does not carry the seed's newer 30s+60s time series.
    sample_after["observation"] = None
    sample_after["sample_windows"] = []
    sample_after["window"]["completed_ios"] = sample_before["window"]["completed_ios"] * 4
    sample_after["threads"] = deepcopy(sample_before["threads"])
    for row in sample_after["threads"]:
        for metric in ("cpu_time_ms", "runqueue_wait_ms", "instructions", "cycles"):
            row[metric] *= 4
    import_batch(client, before)
    import_batch(client, after)
    result = client.post(url(scenario="nfs-low-read") + "/analyze").json()
    for row in result["threads"]:
        assert row["derived_after"]["cpu_us_per_io"] == pytest.approx(row["derived_before"]["cpu_us_per_io"])
        assert row["derived_after"]["cpu_pct"] == pytest.approx(row["derived_before"]["cpu_pct"])
    assert not {"scheduling", "cpu_cost"}.intersection(item["kind"] for item in result["analysis"]["candidates"])


def test_seed_process_thread_roles_follow_user_io_flow():
    for batch in seed_batches():
        for snapshot in batch["scenarios"]:
            roles = {(row["process_role"], row["pool"]) for row in snapshot["threads"]}
            assert ("asan-stord", "gfapi-opt") in roles
            assert ("asan-stord", "tierd-core") in roles
            assert ("glusterfsd", "glfsd-net") in roles
            assert not any(row["process_role"] == "tierd" or row["pool"] in ("ior-workers", "gfapi-callback") for row in snapshot["threads"])
