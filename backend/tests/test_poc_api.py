"""POC routes preserve scope, validation, history and existing app services."""

from copy import deepcopy

from fastapi.testclient import TestClient

from backend.main import create_app


DEFINITION = {
    "id": "test.bdev_depth", "label": "测试深度", "component": "bdev",
    "value_type": "integer", "unit": "I/O", "protocols": ["vhost"],
    "description": "仅测试接口，不映射真实产品参数。", "min": 1, "max": 16,
    "device_requirements": {"platform": "arm"},
}
CAMPAIGN = {
    "id": "api-poc-scope", "title": "测试设备与 fio 范围", "source": "measured",
    "environment": {"id": "test-device", "label": "测试环境", "hardware": {"platform": "arm"}},
    "build": "test-build", "protocol": "vhost",
    "fio": {"rw": "randwrite", "bs": "4k", "iodepth": 8, "numjobs": 1},
    "goal": {"metric": "iops", "p99_limit_ms": 2, "min_repeats": 3},
    "baseline_parameters": {"test.bdev_depth": 8},
}


def trial(identifier, depth=8, iops=1000):
    return {
        "id": identifier, "environment_id": CAMPAIGN["environment"]["id"],
        "build": CAMPAIGN["build"], "protocol": CAMPAIGN["protocol"],
        "fio": deepcopy(CAMPAIGN["fio"]), "source": "measured",
        "parameters": {"test.bdev_depth": depth},
        "metrics": {"iops": iops, "bandwidth_mib_s": 4, "latency_ms": 0.5, "p99_ms": 1},
    }


def setup_campaign(api):
    assert api.post("/api/poc/parameters", json=DEFINITION).status_code == 201
    created = api.post("/api/poc/campaigns", json=CAMPAIGN)
    assert created.status_code == 201, created.text
    return "/api/poc/campaigns/" + created.json()["id"]


def test_poc_api_roundtrip_and_durable_analysis_review(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as api:
        path = setup_campaign(api)
        assert api.get("/api/health").json()["lab_connected"] is False
        assert api.get("/api/version/batches").status_code == 200
        assert api.get("/api/cases").status_code == 200
        for index in range(3):
            for depth, iops in ((8, 1000), (4, 1200)):
                response = api.post(path + "/trials", json=trial(f"trial-{depth}-{index}", depth, iops))
                assert response.status_code == 201, response.text
        response = api.post(path + "/analyze", json={})
        assert response.status_code == 200, response.text
        campaign = api.get(path).json()
        analysis = campaign["analysis"]
        assert analysis["framework"] == "LangGraph"
        assert len(analysis["steps"]) == 4
        assert analysis["recommendation"]["parameters"] == {"test.bdev_depth": 4}
        review = {"analysis_id": analysis["id"], "verdict": "confirmed", "note": "测试：独立重复支持结果", "reviewer": "接口测试"}
        assert api.post(path + "/feedback", json=review).status_code == 200
        assert api.post(path + "/analyze", json={}).status_code == 200
        updated = api.get(path).json()
        histories = {record["id"]: record for record in updated["analysis_history"]}
        assert histories[analysis["id"]]["recommendation"]["verdict"] == "confirmed"
        assert histories[analysis["id"]]["recommendation"]["review_history"][0]["note"] == review["note"]
    with TestClient(create_app(tmp_path, seed=False)) as api:
        restored = api.get(path).json()
        assert len(restored["trials"]) == 6
        assert restored["parameter_definitions"][0]["id"] == DEFINITION["id"]
        assert len(restored["analysis_history"]) >= 2


def test_poc_api_rejects_scope_mismatch_and_partial_parameter_sets(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as api:
        path = setup_campaign(api)
        for field, value in (("environment_id", "other-device"), ("build", "other-build"),
                             ("protocol", "nfs"), ("source", "mock"),
                             ("fio", {**CAMPAIGN["fio"], "iodepth": 64}),
                             ("parameters", {}), ("parameters", {"test.bdev_depth": True}),
                             ("parameters", {"test.bdev_depth": 32})):
            payload = trial("invalid")
            payload[field] = value
            result = api.post(path + "/trials", json=payload)
            assert result.status_code == 422, (field, value, result.text)
        assert api.get(path).json()["trials"] == []
        definition = deepcopy(DEFINITION)
        definition["description"] = "新定义不可覆盖已有参数口径。"
        assert api.post("/api/poc/parameters", json=definition).status_code == 422
        changed = deepcopy(CAMPAIGN)
        changed["id"] = "wrong-device-capability"
        changed["environment"]["hardware"]["platform"] = "x86"
        assert api.post("/api/poc/campaigns", json=changed).status_code == 422
        assert api.get("/api/poc/campaigns/missing-task").status_code == 404


def test_no_recommendation_without_baseline_repeats(tmp_path):
    with TestClient(create_app(tmp_path, seed=False)) as api:
        path = setup_campaign(api)
        for index in range(3):
            assert api.post(path + "/trials", json=trial(f"candidate-{index}", 4, 1200)).status_code == 201
        assert api.post(path + "/analyze", json={}).status_code == 200
        assert api.get(path).json()["analysis"]["recommendation"] is None
