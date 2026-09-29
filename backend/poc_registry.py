"""Typed parameter registry checks independent of any tuning adapter."""

import json
import math

from .poc_models import ParameterDefinition


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def validate_parameter_value(definition, value):
    kind = definition["value_type"]
    valid = {"integer": type(value) is int,
             "number": type(value) in (int, float) and math.isfinite(value),
             "boolean": type(value) is bool, "string": type(value) is str,
             "enum": any(type(value) is type(choice) and value == choice for choice in definition["choices"])}[kind]
    if not valid:
        raise ValueError(f"参数 {definition['id']} 的值不符合 {kind} 类型或明确 choices。")
    if kind in ("integer", "number"):
        if not math.isfinite(value) or definition["min"] is not None and value < definition["min"] or definition["max"] is not None and value > definition["max"]:
            raise ValueError(f"参数 {definition['id']} 超出注册范围。")


def validate_configuration(definitions, parameters, protocol, hardware, fio):
    if set(parameters) != {definition["id"] for definition in definitions}:
        raise ValueError("参数配置必须包含完整的基线参数 keys；不允许未知参数、遗漏或隐式默认。")
    for definition in definitions:
        if protocol not in definition["protocols"]:
            raise ValueError(f"参数 {definition['id']} 不支持协议 {protocol}。")
        for name, actual in (("device_requirements", hardware), ("fio_requirements", fio)):
            for key, expected in definition[name].items():
                if key not in actual or actual[key] is None or canonical(actual[key]) != canonical(expected):
                    raise ValueError(f"参数 {definition['id']} 的 {name}.{key} 不满足；缺失信息不能视为符合。")
        validate_parameter_value(definition, parameters[definition["id"]])


def normalized_definition(value):
    model = value if isinstance(value, ParameterDefinition) else ParameterDefinition.model_validate(value)
    payload = model.model_dump(mode="json")
    if len({canonical(choice) for choice in payload["choices"]}) != len(payload["choices"]):
        raise ValueError("Parameter choices must be unique")
    return payload
