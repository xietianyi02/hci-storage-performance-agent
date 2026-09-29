"""Synthetic collector output crosses the real import, pairing and graph boundary."""

from copy import deepcopy
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.version_models import Batch
from collectors.merge import build_batch
from collectors.tests.test_collectors import FIO, METADATA, SCENARIO, WINDOW, bundle


def measured_batch(identifier, *, later=False, aligned=True):
    observations = [bundle(role) for role in ("guest", "local", "remote")]
    window = deepcopy(WINDOW)
    metadata = deepcopy(METADATA)
    metadata["id"] = identifier
    fio = deepcopy(FIO)
    if later:
        for field in ("started_at", "ended_at"):
            window[field] = (datetime.fromisoformat(window[field]) + timedelta(minutes=10)).isoformat()
        window["completed_ios"] = 700
        fio["jobs"][0]["write"].update(total_ios=700, iops=70)
        fio["jobs"][0]["write"]["lat_ns"]["mean"] = 1400000
    for observation in observations:
        observation.update(started_at=window["started_at"], ended_at=window["ended_at"])
    observations[1]["threads"] = [{
        "host_role": "local", "process_role": "asan-stord", "pool": "gfapi-opt", "branch": "business",
        "thread_count": 1, "cpu_time_ms": 2000 if later else 1500,
        "instructions": 100000, "cycles": 200000, "runqueue_wait_ms": None,
        "context_switches": 20, "migrations": 0, "cpu_ids": [0], "allowed_cpu_ids": [0],
        "numa_nodes": [], "memory_numa_nodes": [], "remote_access_pct": None,
        "counting_ratio": 1, "counter_scope": "synthetic same-window fixture", "top_functions": [],
    }]
    return build_batch(observations, metadata, SCENARIO, fio, fio_window=window if aligned else None)


def test_collector_output_imports_pairs_and_drives_graph(tmp_path):
    first = measured_batch("measured-first")
    second = measured_batch("measured-second", later=True)
    # No lab collection occurs: fixtures are explicitly synthetic test evidence.
    assert Batch.model_validate(first).source == "measured"
    with TestClient(create_app(tmp_path, seed=False)) as client:
        for payload in (first, second):
            assert client.post("/api/version/batches", json=payload).status_code == 201
        path = "/api/version/comparisons/measured-second/nfs-write"
        comparison = client.get(path).json()
        assert comparison["previous_batch"]["id"] == "measured-first"
        assert comparison["deltas"]["iops_pct"] == -30
        thread = comparison["threads"][0]
        assert thread["derived_before"]["cpu_us_per_io"] == 1500
        assert round(thread["derived_after"]["cpu_us_per_io"], 3) == 2857.143
        assert thread["derived_after"]["ipc"] == 0.5
        analysis = client.post(path + "/analyze", json={}).json()["analysis"]
        assert all(step["status"] == "completed" for step in analysis["steps"])
        assert any(candidate["kind"] == "cpu_cost" for candidate in analysis["candidates"])


def test_unaligned_collector_import_keeps_cost_unknown(tmp_path):
    payload = measured_batch("measured-unaligned", aligned=False)
    with TestClient(create_app(tmp_path, seed=False)) as client:
        assert client.post("/api/version/batches", json=payload).status_code == 201
        comparison = client.get("/api/version/comparisons/measured-unaligned/nfs-write").json()
        assert comparison["previous"] is None
        assert comparison["threads"][0]["derived_after"]["cpu_us_per_io"] is None
        assert comparison["threads"][0]["derived_after"]["instructions_per_io"] is None
        assert comparison["threads"][0]["derived_after"]["ipc"] == 0.5
        assert any("MISSING_WINDOW_DENOMINATOR" in message for message in comparison["quality"])
