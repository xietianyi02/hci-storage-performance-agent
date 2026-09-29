"""POC parameter-scoped evidence tests; no device, fio, or SSH execution."""

from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError
import pytest

from backend.poc_models import ParameterDefinition, PocCampaignInput, PocFeedback, PocTrialInput
from backend.poc_seed import demo_campaign, example_parameters
from backend.poc_service import PocService


@pytest.fixture
def service(tmp_path):
    instance = PocService(tmp_path, seed=False)
    for definition in example_parameters():
        instance.register_parameter(ParameterDefinition.model_validate(definition))
    yield instance
    instance.close()


def campaign(service, identifier="test-campaign", **changes):
    payload = demo_campaign()
    payload.update(id=identifier, **changes)
    return service.create_campaign(PocCampaignInput.model_validate(payload))


def trial(scope, parameters=None, **metrics):
    return {"environment_id": scope["environment"]["id"], "build": scope["build"], "protocol": scope["protocol"],
            "source": scope["source"], "fio": deepcopy(scope["fio"]),
            "parameters": deepcopy(parameters or scope["baseline_parameters"]),
            "metrics": {"iops": 100, "bandwidth_mib_s": .4, "latency_ms": .5,
                        "p99_ms": 1, "disk_latency_ms": .2, "tierd_depth": 20, **metrics}}


def add(service, scope, parameters=None, values=(100, 101, 99), **metrics):
    for value in values:
        service.add_trial(scope["id"], PocTrialInput.model_validate(trial(scope, parameters, iops=value, **metrics)))


def definition(identifier="registered.depth", **changes):
    return {"id": identifier, "label": "已登记参数", "component": "bdev", "value_type": "integer",
            "protocols": ["vhost"], "description": "Synthetic validation fixture", "min": 1, "max": 128,
            **changes}


def test_seed_is_explicit_mock_two_host_and_parameter_units_are_unbound(tmp_path):
    instance = PocService(tmp_path)
    try:
        scope = instance.campaigns()[0]
        assert scope["source"] == "mock"
        assert scope["environment"]["hardware"]["data_hosts"] == 2
        assert len(instance.parameters()) == 2
        assert all(item["maturity"] == "example" and item["adapter_key"] is None for item in instance.parameters())
        assert next(item for item in instance.parameters() if item["component"] == "bdev")["unit"] is None
        assert len(scope["trials"]) == 12
        assert len(scope["analysis"]["groups"]) == 4
        assert scope["analysis"]["recommendation"]["parameters"] == {"demo.vhost_coalescing": 4, "demo.bdev_depth": 64}
        assert scope["analysis"]["recommendation"]["verdict"] == "pending"
        assert all("MOCK" in item["note"] for item in scope["trials"])
        assert (tmp_path / "poc.sqlite3").exists()
        assert not (tmp_path / "version_diagnosis.sqlite3").exists()
        assert not (tmp_path / "cases.sqlite3").exists()
    finally:
        instance.close()


@pytest.mark.parametrize("value", [True, 1.5, "64", None, {}, -1, 129])
def test_integer_type_and_registered_range_are_strongly_validated(service, value):
    service.register_parameter(ParameterDefinition.model_validate(definition()))
    with pytest.raises(ValueError):
        campaign(service, baseline_parameters={"registered.depth": value})


def test_parameter_types_enum_unknown_keys_protocol_and_complete_config(service):
    for identifier, kind, options in (("p.flag", "boolean", {}), ("p.mode", "enum", {"choices": ["a", "b"]}),
                                     ("p.label", "string", {}), ("p.rate", "number", {"min": .5, "max": 2.5})):
        payload = definition(identifier, **({"value_type": kind, "min": None, "max": None} | options))
        service.register_parameter(ParameterDefinition.model_validate(payload))
    scope = campaign(service, baseline_parameters={"p.flag": True, "p.mode": "a", "p.label": "ok", "p.rate": 1.5})
    for change in ({"p.flag": 1}, {"p.mode": "c"}, {"p.label": 2}, {"p.rate": True}, {"p.rate": 3}):
        payload = trial(scope)
        payload["parameters"].update(change)
        with pytest.raises(ValueError):
            service.add_trial(scope["id"], PocTrialInput.model_validate(payload))
    payload = trial(scope)
    payload["parameters"].pop("p.label")
    with pytest.raises(ValueError, match="完整"):
        service.add_trial(scope["id"], PocTrialInput.model_validate(payload))
    with pytest.raises(ValueError, match="未注册"):
        campaign(service, "unknown", baseline_parameters={"missing": 1})
    with pytest.raises(ValueError, match="不支持协议"):
        campaign(service, "wrong-protocol", protocol="nfs")


def test_device_fio_requirements_and_parameter_definition_snapshot(service):
    service.register_parameter(ParameterDefinition.model_validate(definition(device_requirements={"cpu_platform": "x86"},
                                                                           fio_requirements={"rw": "randwrite"})))
    payload = demo_campaign()
    payload["id"] = "requirements"
    payload["baseline_parameters"] = {"registered.depth": 16}
    for actual in ({}, {"cpu_platform": None}, {"cpu_platform": "ARM"}):
        payload["environment"]["hardware"] = actual
        with pytest.raises(ValueError, match="device_requirements"):
            service.create_campaign(PocCampaignInput.model_validate(payload))
    payload["environment"]["hardware"] = {"cpu_platform": "x86", "servers": [{"nic": "fixture"}]}
    scope = service.create_campaign(PocCampaignInput.model_validate(payload))
    assert scope["parameter_definitions"][0]["device_requirements"] == {"cpu_platform": "x86"}
    changed = definition(label="changed", device_requirements={"cpu_platform": "ARM"})
    with pytest.raises(ValueError, match="ID 已存在"):
        service.register_parameter(ParameterDefinition.model_validate(changed))
    assert service.detail(scope["id"])["parameter_definitions"] == scope["parameter_definitions"]
    payload["id"] = "fio-requirements"
    payload["fio"]["rw"] = "randread"
    with pytest.raises(ValueError, match="fio_requirements"):
        service.create_campaign(PocCampaignInput.model_validate(payload))


@pytest.mark.parametrize("key,value", [("iodepth", "填写深度"), ("numjobs", True), ("vm_count", 0),
                                      ("direct", True), ("direct", 2), ("rw", " "), ("bs", 4096), ("ioengine", "")])
def test_known_fio_fields_reject_placeholders_and_invalid_types(service, key, value):
    payload = demo_campaign()
    payload["fio"][key] = value
    with pytest.raises(ValidationError, match="fio"):
        PocCampaignInput.model_validate(payload)
    scope = campaign(service)
    row = trial(scope)
    row["fio"][key] = value
    with pytest.raises(ValidationError, match="fio"):
        PocTrialInput.model_validate(row)


def test_complex_fio_metadata_is_preserved_without_claiming_jobfile_validation(service):
    supplied = {"jobfile": {"path": "fixture.fio", "sha256": "fixture-only"}, "jobs": [{"name": "custom", "runtime": 60}]}
    scope = campaign(service, fio=supplied)
    assert scope["fio"] == supplied
    service.add_trial(scope["id"], PocTrialInput.model_validate(trial(scope)))
    assert service.detail(scope["id"])["trials"][0]["fio"] == supplied


@pytest.mark.parametrize("field,value", [("environment_id", "other"), ("build", "other"), ("protocol", "iscsi"),
                                         ("source", "measured"), ("fio", {"rw": "randread"})])
def test_trial_cannot_mix_devices_version_protocol_source_or_fio(service, field, value):
    scope = campaign(service)
    payload = trial(scope)
    payload[field] = value
    with pytest.raises(ValueError, match="同一环境"):
        service.add_trial(scope["id"], PocTrialInput.model_validate(payload))
    assert service.detail(scope["id"])["trials"] == []


def test_trials_and_campaigns_are_immutable_and_analysis_remains_campaign_local(service):
    first = campaign(service, "first")
    second = campaign(service, "second")
    payload = trial(first)
    payload["id"] = "fixed-trial"
    service.add_trial(first["id"], PocTrialInput.model_validate(payload))
    with pytest.raises(ValueError, match="不可覆盖"):
        service.add_trial(first["id"], PocTrialInput.model_validate(payload))
    with pytest.raises(ValueError, match="不可覆盖"):
        campaign(service, "first")
    assert len(service.detail(first["id"])["trials"]) == 1
    assert service.analyze(second["id"])["analysis"]["recommendation"] is None


def test_langgraph_groups_by_complete_config_uses_medians_and_records_noise(service):
    scope = campaign(service)
    candidate = scope["baseline_parameters"] | {"demo.vhost_coalescing": 4}
    add(service, scope, values=(100, 500, 101))
    add(service, scope, candidate, values=(105, 106, 10000))
    result = service.analyze(scope["id"])["analysis"]
    assert result["framework"] == "LangGraph"
    assert {"validate", "group_trials", "compare_candidates", "report"}.issubset(service.graph.get_graph().nodes)
    assert [step["id"] for step in result["steps"]] == ["validate", "group_trials", "compare_candidates", "report"]
    assert all(step["status"] == "completed" and step["detail"] for step in result["steps"])
    baseline = next(group for group in result["groups"] if group["is_baseline"])
    assert baseline["medians"]["iops"] == 101
    assert baseline["range"]["iops"] == {"min": 100, "max": 500, "known": 3, "total": 3}
    assert result["recommendation"]["observed_value"] == 106
    assert result["recommendation"]["verdict"] == "pending"


def test_missing_baseline_or_sufficient_comparison_produces_no_recommendation(service):
    scope = campaign(service)
    candidate = scope["baseline_parameters"] | {"demo.vhost_coalescing": 4}
    add(service, scope, candidate)
    assert service.analyze(scope["id"])["analysis"]["recommendation"] is None
    add(service, scope, values=(100, 99))
    assert service.analyze(scope["id"])["analysis"]["recommendation"] is None
    fresh = campaign(service, "baseline-only")
    add(service, fresh)
    assert service.analyze(fresh["id"])["analysis"]["recommendation"] is None
    add(service, fresh, candidate, values=(105, 106))
    assert service.analyze(fresh["id"])["analysis"]["recommendation"] is None


def test_p99_is_checked_on_every_repeat_and_missing_values_do_not_become_zero(service):
    scope = campaign(service)
    add(service, scope)
    candidate = scope["baseline_parameters"] | {"demo.vhost_coalescing": 4}
    add(service, scope, candidate, values=(200, 201), p99_ms=.8)
    service.add_trial(scope["id"], PocTrialInput.model_validate(trial(scope, candidate, iops=202, p99_ms=None, disk_latency_ms=None)))
    result = service.analyze(scope["id"])["analysis"]
    group = next(group for group in result["groups"] if not group["is_baseline"])
    assert group["eligible"] is False
    assert group["medians"]["p99_ms"] is None
    assert group["range"]["p99_ms"]["known"] == 2
    assert group["medians"]["disk_latency_ms"] is None
    assert result["recommendation"]["keep_baseline"] is True
    other = campaign(service, "one-tail-failure")
    add(service, other)
    add(service, other, candidate, values=(200, 201), p99_ms=.8)
    service.add_trial(other["id"], PocTrialInput.model_validate(trial(other, candidate, iops=202, p99_ms=2)))
    bad = next(group for group in service.analyze(other["id"])["analysis"]["groups"] if not group["is_baseline"])
    assert bad["eligible"] is False  # Median p99=.8 would hide the failed repeat.


def test_latency_goal_minimizes_and_unset_tail_limit_is_explicit(service):
    scope = campaign(service, goal={"metric": "latency_ms", "p99_limit_ms": None, "min_repeats": 3})
    candidate = scope["baseline_parameters"] | {"demo.vhost_coalescing": 4}
    add(service, scope, latency_ms=.5, p99_ms=None)
    add(service, scope, candidate, latency_ms=.3, p99_ms=None)
    analysis = service.analyze(scope["id"])["analysis"]
    assert analysis["recommendation"]["parameters"] == candidate
    assert analysis["recommendation"]["change_pct"] == -40
    assert "未设置时延上限" in analysis["summary"]
    assert "未设置时延上限" in analysis["recommendation"]["scope_note"]


def test_zero_baseline_denominator_does_not_produce_infinity(service):
    scope = campaign(service)
    candidate = scope["baseline_parameters"] | {"demo.vhost_coalescing": 4}
    add(service, scope, values=(0, 0, 0))
    add(service, scope, candidate)
    assert service.analyze(scope["id"])["analysis"]["recommendation"]["change_pct"] is None


def test_baseline_better_than_all_qualified_configs_is_kept(service):
    scope = campaign(service)
    add(service, scope, values=(200, 201, 199))
    add(service, scope, scope["baseline_parameters"] | {"demo.bdev_depth": 128}, values=(100, 101, 99))
    result = service.analyze(scope["id"])["analysis"]
    assert result["best_observed_group_id"] == result["baseline_group_id"]
    assert result["recommendation"]["keep_baseline"] is True
    assert "保持基线" in result["summary"]


def test_depth_association_requires_other_parameters_fixed_and_two_values(service):
    scope = campaign(service)
    add(service, scope, disk_latency_ms=.3, tierd_depth=40)
    add(service, scope, {"demo.vhost_coalescing": 4, "demo.bdev_depth": 128}, disk_latency_ms=.8, tierd_depth=90)
    assert service.analyze(scope["id"])["analysis"]["correlations"] == []
    add(service, scope, {"demo.vhost_coalescing": 2, "demo.bdev_depth": 128}, values=(80, 79, 81), disk_latency_ms=.8, tierd_depth=90)
    correlations = service.analyze(scope["id"])["analysis"]["correlations"]
    depth = next(item for item in correlations if item["parameter_id"] == "demo.bdev_depth")
    assert depth["fixed_parameters"] == {"demo.vhost_coalescing": 2}
    assert {"iops", "disk_latency_ms", "tierd_depth"}.issubset(depth["observables"])
    assert depth["causal"] is False
    assert "不证明因果" in depth["summary"]
    assert depth["points"][1]["medians"]["iops"] < depth["points"][0]["medians"]["iops"]


def test_analysis_history_and_reviews_survive_new_trials_reanalysis_and_restart(tmp_path):
    instance = PocService(tmp_path)
    scope = instance.campaigns()[0]
    first_id = scope["analysis"]["id"]
    instance.feedback(scope["id"], PocFeedback(analysis_id=first_id, verdict="rejected", note="模拟评价：需真实设备验证", reviewer="专家"))
    instance.add_trial(scope["id"], PocTrialInput.model_validate(trial(scope)))
    assert instance.detail(scope["id"])["analysis"] is None
    updated = instance.analyze(scope["id"])
    assert updated["analysis"]["id"] != first_id
    assert updated["analysis"]["recommendation"]["verdict"] == "pending"
    assert updated["analysis_history"][0]["recommendation"]["verdict"] == "rejected"
    assert len(updated["analysis_history"]) == 2
    instance.close()
    reopened = PocService(tmp_path)
    try:
        preserved = reopened.detail(scope["id"])
        assert len(preserved["analysis_history"]) == 2
        assert len(preserved["trials"]) == 13
        assert preserved["analysis_history"][0]["recommendation"]["review_history"][0]["note"] == "模拟评价：需真实设备验证"
    finally:
        reopened.close()


def test_feedback_requires_campaign_scoped_analysis_and_nonblank_expert_reason(service):
    first, second = campaign(service, "one"), campaign(service, "two")
    add(service, first)
    add(service, first, first["baseline_parameters"] | {"demo.vhost_coalescing": 4})
    analysis = service.analyze(first["id"])["analysis"]
    with pytest.raises(ValueError, match="不属于本实验"):
        service.feedback(second["id"], PocFeedback(analysis_id=analysis["id"], verdict="confirmed", note="reason", reviewer="expert"))
    with pytest.raises(ValidationError, match="nonblank"):
        PocFeedback(analysis_id=analysis["id"], verdict="confirmed", note="  ", reviewer="expert")
    reviewed = service.feedback(first["id"], PocFeedback(analysis_id=analysis["id"], verdict="pending", note="先补A/B实测", reviewer="专家"))
    assert reviewed["analysis"]["recommendation"]["review_history"][0]["verdict"] == "pending"
