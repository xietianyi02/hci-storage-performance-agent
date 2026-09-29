"""Light navigation projections preserve immutable pairing and full evidence."""

from copy import deepcopy
import json
import sqlite3

from fastapi.testclient import TestClient
import pytest

from backend.main import create_app
from backend.version_models import Batch
from backend.version_seed import seed_batches
from backend.version_service import VersionService


def import_seeds(service):
    for payload in light_seeds():
        service.import_batch(Batch.model_validate(payload))


def light_seeds():
    batches = seed_batches()
    for batch in batches:
        for sample in batch["scenarios"]:
            sample.update(observation=None, sample_windows=[], cpu_inventory=[])
    return batches


def forbid_raw_batches(*args, **kwargs):
    raise AssertionError("Navigation or cached comparison decoded raw batch evidence")


def test_navigation_uses_small_projections_not_raw_batches(tmp_path, monkeypatch):
    service = VersionService(tmp_path, seed=False)
    import_seeds(service)
    monkeypatch.setattr(service.store, "list_batches", forbid_raw_batches)
    monkeypatch.setattr(service.store, "get_batch", forbid_raw_batches)
    monkeypatch.setattr(service.store, "get_snapshot", forbid_raw_batches)
    catalog = service.catalog()
    assert len(catalog) == 3
    assert all(len(batch["scenarios"]) == 4 for batch in catalog)
    fields = {"id", "title", "protocol", "fio", "window", "metrics"}
    assert all(set(sample) == fields for batch in catalog for sample in batch["scenarios"])
    summaries = service.scenario_summaries("VB-DEMO-29")
    assert len(summaries) == 4
    assert all(item["previous_batch"]["id"] == "VB-DEMO-28" for item in summaries)
    assert all(set(item["current"]) == fields and set(item["previous"]) == fields for item in summaries)
    assert "sample_windows" not in json.dumps(catalog)
    assert "cpu_inventory" not in json.dumps(summaries)
    assert len(json.dumps(catalog).encode()) < 15000
    assert service.store.connection.execute("SELECT COUNT(*) FROM version_comparisons").fetchone()[0] == 0
    with pytest.raises(KeyError):
        service.scenario_summaries("missing")
    service.close()


def test_uncached_pair_loads_only_two_target_snapshots_and_cache_loads_none(tmp_path, monkeypatch):
    service = VersionService(tmp_path, seed=False)
    import_seeds(service)
    original = service.store.get_snapshot
    calls = []

    def capture(batch_id, scenario_id):
        calls.append((batch_id, scenario_id))
        return original(batch_id, scenario_id)

    monkeypatch.setattr(service.store, "get_snapshot", capture)
    monkeypatch.setattr(service.store, "list_batches", forbid_raw_batches)
    monkeypatch.setattr(service.store, "get_batch", forbid_raw_batches)
    result = service.comparison("VB-DEMO-29", "nfs-high-write")
    assert calls == [("VB-DEMO-29", "nfs-high-write"), ("VB-DEMO-28", "nfs-high-write")]
    summary = next(item for item in service.scenario_summaries("VB-DEMO-29") if item["current"]["id"] == "nfs-high-write")
    assert summary["id"] == result["id"] and summary["deltas"] == result["deltas"]
    monkeypatch.setattr(service.store, "get_snapshot", forbid_raw_batches)
    assert service.comparison("VB-DEMO-29", "nfs-high-write") == result
    lean = service.comparison("VB-DEMO-29", "nfs-high-write", include_samples=False)
    assert "sample_windows" not in lean["current"] and "cpu_inventory" not in lean["current"]
    assert service.store.get_comparison(result["id"]) == result
    service.close()


def test_backfill_adds_only_projections_and_retains_analysis_review_payloads(tmp_path):
    service = VersionService(tmp_path, seed=False)
    import_seeds(service)
    comparison = service.analyze("VB-DEMO-29", "nfs-high-write")
    candidate = comparison["analysis"]["candidates"][0]
    from backend.version_models import VersionFeedback
    reviewed = service.feedback("VB-DEMO-29", "nfs-high-write", VersionFeedback(
        analysis_id=comparison["analysis"]["id"], candidate_id=candidate["id"], verdict="pending",
        note="原模拟记录，等待复测", reviewer="测试专家"))
    raw_batches = list(service.store.connection.execute("SELECT id,payload FROM version_batches ORDER BY id"))
    raw_comparisons = list(service.store.connection.execute("SELECT id,payload FROM version_comparisons ORDER BY id"))
    expected_batches = [tuple(row) for row in raw_batches]
    expected_comparisons = [tuple(row) for row in raw_comparisons]
    service.close()
    with sqlite3.connect(tmp_path / "version_diagnosis.sqlite3") as connection:
        connection.execute("DROP TABLE version_scenario_catalog")
        connection.execute("DROP TABLE version_batch_catalog")
    restarted = VersionService(tmp_path, seed=False)
    assert [tuple(row) for row in restarted.store.connection.execute("SELECT id,payload FROM version_batches ORDER BY id")] == expected_batches
    assert [tuple(row) for row in restarted.store.connection.execute("SELECT id,payload FROM version_comparisons ORDER BY id")] == expected_comparisons
    assert len(restarted.catalog()) == 3
    assert restarted.comparison("VB-DEMO-29", "nfs-high-write") == reviewed
    restarted.close()


@pytest.mark.parametrize("a_started,z_started,expected", [
    ("2026-09-29T11:00:00+08:00", "2026-09-29T03:01:00+00:00", "VB-Z"),
    ("2026-09-29T11:02:00+08:00", "2026-09-29T03:01:00+00:00", "VB-A"),
    ("2026-09-29T11:00:00+08:00", "2026-09-29T03:00:00+00:00", "VB-Z"),
])
def test_pairing_preserves_real_time_strict_predecessor_and_tiebreaks(tmp_path, a_started, z_started, expected):
    before, _, current = light_seeds()
    service = VersionService(tmp_path, seed=False)
    for batch_id, tested_at, started_at in (
        ("VB-A", "2026-09-29T10:00:00+08:00", a_started),
        ("VB-Z", "2026-09-29T02:00:00+00:00", z_started),
        ("VB-SAME-TIME", "2026-09-29T12:00:00+08:00", z_started),
    ):
        candidate = deepcopy(before)
        candidate.update(id=batch_id, created_at=tested_at)
        sample = candidate["scenarios"][0]
        sample["id"] = "alpha"
        sample["window"]["started_at"] = started_at
        candidate["scenarios"] = [sample, {**deepcopy(sample), "id": "zeta"}]
        service.import_batch(Batch.model_validate(candidate))
    current["created_at"] = "2026-09-29T04:00:00+00:00"
    current["scenarios"] = [current["scenarios"][0]]
    service.import_batch(Batch.model_validate(current))
    summary = service.scenario_summaries(current["id"])[0]
    assert summary["previous_batch"]["id"] == expected
    assert summary["previous"]["id"] == "zeta"
    result = service.comparison(current["id"], current["scenarios"][0]["id"])
    assert result["id"] == summary["id"]
    service.close()


def test_lean_api_never_removes_stored_samples_or_feedback_history(tmp_path):
    # The collector fixture is small and has genuine missing-value distinctions.
    from backend.tests.test_version_time_series import timed_batch
    payload = timed_batch()
    with TestClient(create_app(tmp_path, seed=False)) as api:
        assert api.post("/api/version/batches", json=payload).status_code == 201
        catalog = api.get("/api/version/catalog")
        assert catalog.status_code == 200
        path = "/api/version/comparisons/fullfio-1/nfs-write"
        full = api.get(path).json()
        lean = api.get(path + "?include_samples=false").json()
        assert len(full["current"]["sample_windows"]) > 0
        assert "sample_windows" not in lean["current"] and "cpu_inventory" not in lean["current"]
        for field in ("observation", "timeline", "threads", "cpus", "network", "metrics"):
            assert lean["current"][field] == full["current"][field]
        analyzed = api.post(path + "/analyze?include_samples=false").json()
        assert "sample_windows" not in analyzed["current"]
        assert analyzed["analysis"]["framework"] == "LangGraph"
        assert all(step["status"] == "completed" for step in analyzed["analysis"]["steps"])
        reread = api.get(path).json()
        assert reread["current"]["sample_windows"] == full["current"]["sample_windows"]
        assert reread["analysis"] == analyzed["analysis"]
        assert api.get("/api/version/batches/missing/scenarios").status_code == 404
        assert api.get(path + "?include_samples=nonsense").status_code == 422
    with TestClient(create_app(tmp_path, seed=False)) as api:
        assert api.get(path).json()["current"]["sample_windows"] == full["current"]["sample_windows"]


def test_lean_feedback_returns_current_history_without_mutating_full_payload(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as api:
        for payload in seed_batches():
            payload["scenarios"] = [payload["scenarios"][3]]
            payload["scenarios"][0]["sample_windows"] = payload["scenarios"][0]["sample_windows"][:1]
            assert api.post("/api/version/batches", json=payload).status_code == 201
        path = "/api/version/comparisons/VB-DEMO-29/nfs-high-write"
        full = api.post(path + "/analyze").json()
        feedback = {"analysis_id": full["analysis"]["id"], "candidate_id": full["analysis"]["candidates"][0]["id"],
                    "verdict": "pending", "note": "模拟流程复核，未真实验证", "reviewer": "测试专家"}
        response = api.post(path + "/feedback?include_samples=false", json=feedback)
        assert response.status_code == 200
        lean = response.json()
        reread = api.get(path).json()
        assert len(lean["analysis"]["candidates"][0]["review_history"]) == 1
        assert lean["analysis"] == reread["analysis"]
        assert reread["current"]["sample_windows"] == full["current"]["sample_windows"]
