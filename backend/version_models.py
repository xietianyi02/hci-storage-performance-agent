"""Normalized batch schema for version comparison, independent of old cases."""

from datetime import datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class EnvironmentProfile(StrictModel):
    id: str = Field(min_length=1, max_length=100)
    label: str = Field(min_length=1, max_length=200)
    description: str = Field(max_length=5000)


class FioModel(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    rw: str = Field(min_length=1, max_length=100)
    bs: str = Field(min_length=1, max_length=100)
    iodepth: int = Field(ge=1, le=4096)
    numjobs: int = Field(ge=1, le=512)
    vm_count: int = Field(ge=1, le=512)
    ioengine: str = Field(min_length=1, max_length=100)
    direct: Literal[0, 1]

    @field_validator("rw", "bs", "ioengine")
    @classmethod
    def normalize_names(cls, value):
        normalized = value.strip().lower()
        if not normalized:
            raise ValueError("Fio name must not be blank")
        return normalized


class SamplingWindow(StrictModel):
    duration_s: float = Field(gt=0, le=86400)
    started_at: datetime
    completed_ios: int | None = Field(ge=0)

    @field_validator("started_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Sampling time requires a timezone")
        return value.astimezone(timezone.utc)


class FioMetrics(StrictModel):
    iops: float = Field(ge=0)
    bandwidth_mib_s: float = Field(ge=0)
    latency_ms: float = Field(ge=0)
    p99_ms: float | None = Field(ge=0)


class TimelinePoint(StrictModel):
    offset_s: float = Field(ge=0)
    iops: float = Field(ge=0)
    latency_ms: float = Field(ge=0)


HostRole = Literal["guest", "local", "remote"]


class TopFunction(StrictModel):
    name: str = Field(min_length=1, max_length=500)
    samples_pct: float = Field(ge=0, le=100)

    @field_validator("name")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Function name must not be blank")
        return value.strip()


class ThreadCapture(StrictModel):
    host_role: HostRole
    process_role: str = Field(min_length=1, max_length=100)
    pool: str = Field(min_length=1, max_length=100)
    branch: str = Field(max_length=100)
    thread_count: int = Field(ge=1, le=100000)
    cpu_time_ms: float | None = Field(ge=0)
    instructions: int | None = Field(ge=0)
    cycles: int | None = Field(ge=0)
    runqueue_wait_ms: float | None = Field(ge=0)
    context_switches: int | None = Field(ge=0)
    migrations: int | None = Field(ge=0)
    cpu_ids: list[NonNegativeInt]
    allowed_cpu_ids: list[NonNegativeInt]
    numa_nodes: list[NonNegativeInt]
    memory_numa_nodes: list[NonNegativeInt]
    remote_access_pct: float | None = Field(ge=0, le=100)
    counting_ratio: float | None = Field(ge=0, le=1)
    counter_scope: str = Field(max_length=200)
    top_functions: list[TopFunction] = Field(max_length=500)


class CpuCapture(StrictModel):
    host_role: HostRole
    cpu_id: int = Field(ge=0)
    numa_node: int | None = Field(ge=0)
    user_pct: float = Field(ge=0, le=100)
    system_pct: float = Field(ge=0, le=100)
    irq_pct: float = Field(ge=0, le=100)
    softirq_pct: float = Field(ge=0, le=100)
    idle_pct: float = Field(ge=0, le=100)
    competitors: list[str]
    smt_sibling_cpu_ids: list[NonNegativeInt] = Field(default_factory=list)
    core_id: int | None = Field(default=None, ge=0)
    socket_id: int | None = Field(default=None, ge=0)
    busy_pct: float | None = Field(default=None, ge=0, le=100)


class CpuTopology(StrictModel):
    host_role: HostRole
    cpu_id: int = Field(ge=0)
    numa_node: int | None = Field(default=None, ge=0)
    socket_id: int | None = Field(default=None, ge=0)
    core_id: int | None = Field(default=None, ge=0)
    smt_sibling_cpu_ids: list[NonNegativeInt] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_siblings(self):
        if len(set(self.smt_sibling_cpu_ids)) != len(self.smt_sibling_cpu_ids) or self.smt_sibling_cpu_ids and self.cpu_id not in self.smt_sibling_cpu_ids:
            raise ValueError("Known SMT sibling IDs must be unique and include the CPU itself")
        return self


class NetworkCapture(StrictModel):
    host_role: HostRole
    interface: str = Field(min_length=1, max_length=100)
    queue: str = Field(min_length=1, max_length=100)
    irq_id: int | None = Field(ge=0)
    cpu_ids: list[NonNegativeInt]
    allowed_cpu_ids: list[NonNegativeInt]
    numa_node: int | None = Field(ge=0)
    irq_per_s: float | None = Field(ge=0)
    packets_per_s: float | None = Field(ge=0)
    drops: int | None = Field(ge=0)
    retransmits: int | None = Field(ge=0)


class Observation(StrictModel):
    started_at: datetime
    warmup_s: float = Field(ge=0, le=86400)
    measurement_s: float = Field(gt=0, le=86400)
    interval_s: float = Field(gt=0, le=86400)

    @field_validator("started_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Observation time requires a timezone")
        return value.astimezone(timezone.utc)


class IndividualThreadSample(StrictModel):
    host_role: HostRole
    process_role: str = Field(min_length=1, max_length=100)
    pool: str = Field(min_length=1, max_length=100)
    branch: str = Field(max_length=100)
    pid: int = Field(gt=0)
    tid: int = Field(gt=0)
    starttime_ticks: int = Field(ge=0)
    name: str = Field(max_length=200)
    state: str = Field(max_length=20)
    cpu_id: int | None = Field(ge=0)
    previous_cpu_id: int | None = Field(ge=0)
    allowed_cpu_ids: list[NonNegativeInt]
    numa_node: int | None = Field(ge=0)
    cpu_pct: float | None = Field(ge=0, le=100)
    instructions: int | None = Field(ge=0)
    cycles: int | None = Field(ge=0)
    counting_ratio: float | None = Field(ge=0, le=1)
    counter_scope: str = Field(max_length=300)
    migrations: int | None = Field(ge=0)
    runqueue_wait_ms: float | None = Field(ge=0)


class LayerSample(StrictModel):
    layer: str = Field(min_length=1, max_length=100)
    scope: str = Field(min_length=1, max_length=300)
    latency_us: float | None = Field(ge=0)
    queue_depth: float | None = Field(ge=0)


class SampleWindow(StrictModel):
    start_offset_s: float = Field(ge=0)
    end_offset_s: float = Field(gt=0)
    phase: Literal["warmup", "measurement"]
    iops: float | None = Field(ge=0)
    completed_ios: int | None = Field(ge=0)
    latency_ms: float | None = Field(ge=0)
    threads: list[IndividualThreadSample] = Field(default_factory=list, max_length=100000)
    cpus: list[CpuCapture] = Field(default_factory=list, max_length=2000)
    network: list[NetworkCapture] = Field(default_factory=list, max_length=1000)
    layers: list[LayerSample] = Field(default_factory=list, max_length=2000)

    @model_validator(mode="after")
    def validate_window(self):
        if self.end_offset_s <= self.start_offset_s:
            raise ValueError("Sample window end must follow start")
        identities = [(row.host_role, row.pid, row.tid, row.starttime_ticks) for row in self.threads]
        cpus = [(row.host_role, row.cpu_id) for row in self.cpus]
        networks = [(row.host_role, row.interface, row.queue) for row in self.network]
        layers = [(row.layer, row.scope) for row in self.layers]
        if any(len(set(keys)) != len(keys) for keys in (identities, cpus, networks, layers)):
            raise ValueError("Duplicate sample identity or capture scope")
        if self.iops is not None and self.completed_ios is not None:
            actual = self.completed_ios / (self.end_offset_s - self.start_offset_s)
            if abs(self.iops - actual) > max(0.01, actual * 0.0001):
                raise ValueError("Sample IOPS conflicts with completed IOs / actual duration")
        return self


class ArtifactReference(StrictModel):
    kind: str = Field(max_length=100)
    path: str = Field(max_length=2000)
    note: str = Field(max_length=5000)


class Snapshot(StrictModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=200)
    protocol: Literal["nfs", "vhost", "iscsi"]
    fio: FioModel
    window: SamplingWindow
    metrics: FioMetrics
    timeline: list[TimelinePoint] = Field(max_length=10000)
    threads: list[ThreadCapture] = Field(max_length=2000)
    cpus: list[CpuCapture] = Field(max_length=2000)
    network: list[NetworkCapture] = Field(max_length=1000)
    quality: list[str]
    artifacts: list[ArtifactReference] = Field(max_length=500)
    observation: Observation | None = None
    sample_windows: list[SampleWindow] = Field(default_factory=list, max_length=10000)
    cpu_inventory: list[CpuTopology] = Field(default_factory=list, max_length=10000)

    @model_validator(mode="after")
    def unique_capture_scopes(self):
        groups = [(row.host_role, row.process_role, row.pool, row.branch) for row in self.threads]
        cpus = [(row.host_role, row.cpu_id) for row in self.cpus]
        networks = [(row.host_role, row.interface, row.queue) for row in self.network]
        if len(set(groups)) != len(groups) or len(set(cpus)) != len(cpus) or len(set(networks)) != len(networks):
            raise ValueError("Duplicate capture scope would double-count metrics")
        inventory = {(row.host_role, row.cpu_id) for row in self.cpu_inventory}
        if len(inventory) != len(self.cpu_inventory):
            raise ValueError("Duplicate CPU inventory scope")
        if inventory and any(key not in inventory for key in cpus):
            raise ValueError("Captured CPU is absent from declared CPU inventory")
        if any(point.offset_s > self.window.duration_s for point in self.timeline):
            raise ValueError("Timeline point exceeds sampling window")
        if self.sample_windows and self.observation is None:
            raise ValueError("Sample windows require observation metadata")
        if self.observation is not None:
            measurement_start = self.observation.started_at + timedelta(seconds=self.observation.warmup_s)
            if abs((measurement_start - self.window.started_at).total_seconds()) > 0.1 or abs(self.observation.measurement_s - self.window.duration_s) > 0.1:
                raise ValueError("Observation measurement phase conflicts with summary sampling window")
        previous_end = 0.0
        for sample in self.sample_windows:
            if inventory and any((row.host_role, row.cpu_id) not in inventory for row in sample.cpus):
                raise ValueError("Sample CPU is absent from declared CPU inventory")
            if sample.start_offset_s < previous_end - 1e-9:
                raise ValueError("Sample windows must be ordered and non-overlapping")
            previous_end = sample.end_offset_s
            observation = self.observation
            if sample.end_offset_s > observation.warmup_s + observation.measurement_s + 1e-9:
                raise ValueError("Sample window exceeds observation duration")
            if sample.phase == "warmup" and sample.end_offset_s > observation.warmup_s + 1e-9:
                raise ValueError("Warmup sample crosses measurement boundary")
            if sample.phase == "measurement" and sample.start_offset_s < observation.warmup_s - 1e-9:
                raise ValueError("Measurement sample overlaps warmup")
        return self


class Batch(StrictModel):
    schema_version: Literal[1]
    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    title: str = Field(min_length=1, max_length=200)
    build: str = Field(min_length=1, max_length=200)
    created_at: datetime
    source: Literal["mock", "measured"]
    environment: EnvironmentProfile
    changes: list[str]
    scenarios: list[Snapshot] = Field(min_length=1, max_length=2000)

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value):
        if value.tzinfo is None:
            raise ValueError("Batch time requires a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def unique_scenarios(self):
        if len({row.id for row in self.scenarios}) != len(self.scenarios):
            raise ValueError("Scenario IDs must be unique within a batch")
        return self


class VersionFeedback(StrictModel):
    analysis_id: str
    candidate_id: str
    verdict: Literal["confirmed", "rejected", "pending"]
    note: str = Field(min_length=1, max_length=5000)
    reviewer: str = Field(min_length=1, max_length=100)

    @field_validator("note", "reviewer")
    @classmethod
    def not_blank(cls, value):
        if not value.strip():
            raise ValueError("Review must not be blank")
        return value.strip()
