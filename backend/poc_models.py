"""Campaign-scoped POC tuning inputs; no device execution is implied."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator


Protocol = Literal["nfs", "vhost", "iscsi"]
Metric = Literal["iops", "bandwidth_mib_s", "latency_ms"]
ParameterValue = bool | int | float | str


def validate_fio_fields(value):
    # Preserve user-provided models and extra/jobfile fields. These checks cover
    # familiar fio settings, not the completeness or execution of a jobfile.
    for key in ("iodepth", "numjobs", "vm_count"):
        if key in value and (type(value[key]) is not int or value[key] <= 0):
            raise ValueError(f"fio {key} requires a positive integer; placeholder strings and booleans are invalid")
    if "direct" in value and (type(value["direct"]) is not int or value["direct"] not in (0, 1)):
        raise ValueError("fio direct requires integer 0 or 1")
    for key in ("rw", "bs", "ioengine"):
        if key in value and (type(value[key]) is not str or not value[key].strip()):
            raise ValueError(f"fio {key} requires a nonblank string")
    return value


class PocModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)


class ParameterDefinition(PocModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    label: str = Field(min_length=1, max_length=200)
    component: str = Field(min_length=1, max_length=100)
    value_type: Literal["integer", "number", "boolean", "enum", "string"]
    unit: str | None = Field(default=None, max_length=100)
    protocols: list[Protocol] = Field(min_length=1, max_length=3)
    description: str = Field(min_length=1, max_length=5000)
    observables: list[str] = Field(default_factory=list, max_length=100)
    choices: list[ParameterValue] = Field(default_factory=list, max_length=500)
    min: float | None = None
    max: float | None = None
    maturity: Literal["example", "registered"] = "registered"
    adapter_key: str | None = Field(default=None, max_length=200)
    device_requirements: dict[str, JsonValue] = Field(default_factory=dict)
    fio_requirements: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("label", "component", "description")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Parameter metadata must not be blank")
        return value.strip()

    @model_validator(mode="after")
    def valid_definition(self):
        if len(set(self.protocols)) != len(self.protocols):
            raise ValueError("Parameter protocols must be unique")
        if self.min is not None and self.max is not None and self.min > self.max:
            raise ValueError("Parameter min must not exceed max")
        if (self.min is not None or self.max is not None) and self.value_type not in ("integer", "number"):
            raise ValueError("Only numeric parameters have min/max")
        if self.value_type == "enum" and not self.choices:
            raise ValueError("Enum parameter requires explicit choices")
        if self.value_type != "enum" and self.choices:
            raise ValueError("choices are only supported for enum parameters")
        return self


class PocEnvironment(PocModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    hardware: dict[str, JsonValue]


class PocGoal(PocModel):
    metric: Metric = "iops"
    p99_limit_ms: float | None = Field(default=None, ge=0)
    min_repeats: int = Field(default=3, ge=3, le=1000)


class PocCampaignInput(PocModel):
    id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=200)
    source: Literal["mock", "measured"]
    environment: PocEnvironment
    build: str = Field(min_length=1, max_length=200)
    protocol: Protocol
    fio: dict[str, JsonValue] = Field(min_length=1)
    goal: PocGoal = Field(default_factory=PocGoal)
    baseline_parameters: dict[str, JsonValue] = Field(min_length=1)

    @field_validator("fio")
    @classmethod
    def valid_fio_fields(cls, value):
        return validate_fio_fields(value)

    @field_validator("title", "build")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Campaign metadata must not be blank")
        return value.strip()


class PocTrialMetrics(PocModel):
    iops: float = Field(ge=0)
    bandwidth_mib_s: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    p99_ms: float | None = Field(default=None, ge=0)
    disk_latency_ms: float | None = Field(default=None, ge=0)
    tierd_depth: float | None = Field(default=None, ge=0)


class PocArtifact(PocModel):
    kind: str = Field(min_length=1, max_length=100)
    path: str = Field(min_length=1, max_length=2000)
    note: str = Field(default="", max_length=5000)


class PocTrialInput(PocModel):
    id: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    environment_id: str = Field(min_length=1, max_length=100)
    build: str = Field(min_length=1, max_length=200)
    protocol: Protocol
    fio: dict[str, JsonValue] = Field(min_length=1)
    source: Literal["mock", "measured"]
    parameters: dict[str, JsonValue] = Field(min_length=1)
    metrics: PocTrialMetrics
    artifacts: list[PocArtifact] = Field(default_factory=list, max_length=500)
    note: str = Field(default="", max_length=5000)

    @field_validator("fio")
    @classmethod
    def valid_fio_fields(cls, value):
        return validate_fio_fields(value)


class PocFeedback(PocModel):
    analysis_id: str = Field(min_length=1, max_length=100)
    verdict: Literal["pending", "confirmed", "rejected"]
    note: str = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=1, max_length=100)

    @field_validator("note", "reviewer")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Expert feedback must include a nonblank reason and reviewer")
        return value.strip()
