"""Validated API inputs; product assumptions are kept explicit."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FioContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rw: Literal["randread", "randwrite", "read", "write", "randrw"] = "randwrite"
    bs: Literal["4k", "8k", "16k", "64k", "128k", "1m"] = "4k"
    iodepth: int = Field(default=64, ge=1, le=1024)
    numjobs: int = Field(default=8, ge=1, le=128)
    vm_count: int = Field(default=1, ge=1, le=128)
    ioengine: str = "libaio"
    direct: int = Field(default=1, ge=0, le=1)


class CreateCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    workflow_id: str = "poc"
    protocol: Literal["nfs", "vhost", "iscsi"] = "nfs"
    description: str = Field(default="", max_length=10000)
    owner: str = Field(default="性能专家", max_length=80)
    fio: FioContext = Field(default_factory=FioContext)
    baseline_version: str = Field(default="V6.0 基线", max_length=100)
    target_version: str = Field(default="V6.1 候选", max_length=100)

    @field_validator("title", "owner")
    @classmethod
    def nonempty_trimmed(cls, value):
        if not value.strip():
            raise ValueError("Field must not be blank")
        return value.strip()


class ApprovalInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: bool = True
    note: str = Field(default="确认在演示环境中执行模拟实验", max_length=2000)
    reviewer: str = Field(default="性能专家", max_length=80)


class FeedbackInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recommendation_id: str
    verdict: Literal["confirmed", "rejected", "pending"]
    note: str = Field(default="", max_length=2000)
    reviewer: str = Field(default="性能专家", max_length=80)
    run_id: str | None = None

    @field_validator("reviewer")
    @classmethod
    def reviewer_trimmed(cls, value):
        if not value.strip():
            raise ValueError("Reviewer must not be blank")
        return value.strip()


class MetricValues(BaseModel):
    iops: float = Field(ge=0)
    bandwidth_mib: float = Field(ge=0)
    latency_ms: float = Field(ge=0)


class FioArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")
    baseline: MetricValues
    current: MetricValues
    source: str


class ProfileArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")
    layers: list[dict]
    source: str


class ExperimentArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")
    before: MetricValues
    after: MetricValues
    source: str
