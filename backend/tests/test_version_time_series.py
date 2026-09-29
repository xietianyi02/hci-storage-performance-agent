"""Time-series import durability, compatibility and invalid-window contracts."""

from copy import deepcopy

from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from backend.main import create_app
from backend.version_models import Batch
from backend.version_seed import scenario
from collectors.merge import build_batch
from collectors.tests.test_collectors import METADATA, SCENARIO, FIO, bundle
from collectors.tests.test_time_series import captured, fio_measurement


def timed_batch():
    return build_batch([captured(role) for role in ("guest", "local", "remote")], METADATA, SCENARIO, fio_measurement(),
                       fio_samples=[{"start_offset_s": 3, "end_offset_s": 6, "completed_ios": 300, "latency_ms": 1},
                                    {"start_offset_s": 6, "end_offset_s": 9, "completed_ios": 600, "latency_ms": .5}])


def test_old_schema_one_import_remains_valid_without_new_fields():
    payload = build_batch([bundle("guest")], METADATA, SCENARIO, FIO)
    payload["scenarios"][0].pop("observation")
    payload["scenarios"][0].pop("sample_windows")
    payload["scenarios"][0].pop("cpu_inventory")
    for cpu in payload["scenarios"][0]["cpus"]:
        for key in ("smt_sibling_cpu_ids", "core_id", "socket_id", "busy_pct"):
            cpu.pop(key)
    snapshot = Batch.model_validate(payload).scenarios[0]
    assert snapshot.observation is None
    assert snapshot.sample_windows == []
    assert snapshot.cpus[0].smt_sibling_cpu_ids == []
    assert snapshot.cpu_inventory == []


def test_new_mock_scenario_declares_and_observes_every_logical_cpu():
    sample = scenario("full-cpu-demo", "明确模拟全 CPU", "randread", 1, 1, 28000, .036)
    expected = {(host, cpu) for host, count in (("guest", 8), ("local", 128), ("remote", 128)) for cpu in range(count)}
    assert {(row["host_role"], row["cpu_id"]) for row in sample["cpu_inventory"]} == expected
    assert {(row["host_role"], row["cpu_id"]) for row in sample["cpus"]} == expected
    assert all({(row["host_role"], row["cpu_id"]) for row in window["cpus"]} == expected for window in sample["sample_windows"])
    assert len(sample["sample_windows"]) == 30
    assert all(not window["layers"] for window in sample["sample_windows"])
    assert any("非已连接环境硬件" in note for note in sample["quality"])


def test_api_import_preserves_individual_windows_and_durable_comparison(tmp_path):
    payload = timed_batch()
    with TestClient(create_app(tmp_path, seed=False)) as api:
        assert api.post("/api/version/batches", json=payload).status_code == 201
        comparison = api.get("/api/version/comparisons/fullfio-1/nfs-write").json()
        windows = comparison["current"]["sample_windows"]
        assert windows[0]["iops"] is None
        assert windows[1]["iops"] == 100
        assert windows[2]["iops"] == 200
        assert windows[1]["threads"][0]["cpu_id"] == 1
        assert windows[1]["threads"][0]["instructions"] is None
        assert windows[1]["cpus"][0]["smt_sibling_cpu_ids"] == [0, 1]
        assert {(row["host_role"], row["cpu_id"]) for row in comparison["current"]["cpu_inventory"]} == {(host, cpu) for host in ("guest", "local", "remote") for cpu in (0, 1)}
        assert comparison["current"]["window"]["duration_s"] == 6
        assert comparison["current"]["window"]["completed_ios"] == 900
        assert any("整轮平均值" in note for note in comparison["quality"])
    with TestClient(create_app(tmp_path, seed=False)) as api:
        assert api.get("/api/version/comparisons/fullfio-1/nfs-write").json()["current"]["sample_windows"] == windows


@pytest.mark.parametrize("mutation,message", [
    (lambda sample: sample["sample_windows"][1].update(start_offset_s=2, iops=None), "non-overlapping"),
    (lambda sample: sample["sample_windows"][0].update(phase="measurement"), "overlaps warmup"),
    (lambda sample: sample["sample_windows"][0].update(end_offset_s=4), "crosses measurement"),
    (lambda sample: sample["sample_windows"][0]["threads"].append(deepcopy(sample["sample_windows"][0]["threads"][0])), "Duplicate sample"),
    (lambda sample: sample["sample_windows"][1].update(iops=900), "IOPS conflicts"),
    (lambda sample: sample["observation"].update(measurement_s=60), "conflicts with summary"),
    (lambda sample: sample.update(observation=None), "require observation"),
    (lambda sample: sample["cpu_inventory"].append(deepcopy(sample["cpu_inventory"][0])), "Duplicate CPU inventory"),
    (lambda sample: sample["cpu_inventory"].pop(0), "absent from declared CPU inventory"),
    (lambda sample: sample["cpu_inventory"][0].update(smt_sibling_cpu_ids=[9]), "include the CPU itself"),
])
def test_import_rejects_ambiguous_or_conflicting_windows(mutation, message):
    payload = timed_batch()
    mutation(payload["scenarios"][0])
    with pytest.raises(ValidationError, match=message):
        Batch.model_validate(payload)
