export type HostRole = 'guest' | 'local' | 'remote';
export type VersionSource = 'mock' | 'measured';
export type VersionVerdict = 'confirmed' | 'rejected' | 'pending';
export interface ThreadCapture {
  host_role: HostRole; process_role: string; pool: string; branch: string;
  thread_count: number; cpu_time_ms: number | null; instructions: number | null; cycles: number | null;
  runqueue_wait_ms: number | null; context_switches: number | null; migrations: number | null;
  cpu_ids: number[]; allowed_cpu_ids: number[]; numa_nodes: number[]; memory_numa_nodes: number[];
  remote_access_pct: number | null; counting_ratio: number | null; counter_scope: string;
  top_functions: {name: string; samples_pct: number}[];
}
export interface CpuCapture {
  host_role: HostRole; cpu_id: number; numa_node: number | null;
  user_pct: number; system_pct: number; irq_pct: number; softirq_pct: number; idle_pct: number;
  competitors: string[];
  smt_sibling_cpu_ids?: number[]; core_id?: number | null; socket_id?: number | null;
  busy_pct?: number | null;
}
export interface CpuTopology {
  host_role: HostRole; cpu_id: number; numa_node: number | null;
  socket_id: number | null; core_id: number | null; smt_sibling_cpu_ids: number[];
}
export interface NetworkCapture {
  host_role: HostRole; interface: string; queue: string; irq_id: number | null;
  cpu_ids: number[]; allowed_cpu_ids: number[]; numa_node: number | null;
  irq_per_s: number | null; packets_per_s: number | null; drops: number | null; retransmits: number | null;
}
export interface IndividualThreadSample {
  host_role: HostRole; process_role: string; pool: string; branch: string;
  pid: number; tid: number; starttime_ticks: number; name: string; state: string;
  cpu_id: number | null; previous_cpu_id: number | null; allowed_cpu_ids: number[];
  numa_node: number | null; cpu_pct: number | null;
  instructions: number | null; cycles: number | null; counting_ratio: number | null;
  counter_scope: string; migrations: number | null; runqueue_wait_ms: number | null;
}
export interface LayerSample {
  layer: string; scope: string; latency_us: number | null; queue_depth: number | null;
}
export interface ObservationPlan {
  started_at: string; warmup_s: number; measurement_s: number; interval_s: number;
}
export interface SampleWindow {
  start_offset_s: number; end_offset_s: number; phase: 'warmup' | 'measurement';
  iops: number | null; completed_ios: number | null; latency_ms: number | null;
  threads: IndividualThreadSample[]; cpus: CpuCapture[]; network: NetworkCapture[];
  layers: LayerSample[];
}
export interface VersionSnapshot {
  id: string; title: string; protocol: 'nfs' | 'vhost' | 'iscsi';
  fio: {rw: string; bs: string; iodepth: number; numjobs: number; vm_count: number; ioengine: string; direct: number; [key: string]: unknown};
  window: {duration_s: number; started_at: string; completed_ios: number | null};
  metrics: {iops: number; bandwidth_mib_s: number; latency_ms: number; p99_ms: number | null};
  timeline: {offset_s: number; iops: number; latency_ms: number}[];
  observation?: ObservationPlan | null; sample_windows?: SampleWindow[]; cpu_inventory?: CpuTopology[];
  threads: ThreadCapture[]; cpus: CpuCapture[]; network: NetworkCapture[];
  quality: string[]; artifacts: {kind: string; path: string; note: string}[];
}
export interface VersionBatch {
  schema_version: 1; id: string; title: string; build: string; created_at: string;
  source: VersionSource; environment: {id: string; label: string; description: string};
  changes: string[]; scenarios: VersionSnapshot[];
}
export type VersionSnapshotSummary = Pick<VersionSnapshot, 'id' | 'title' | 'protocol' | 'fio' | 'window' | 'metrics'>;
export type VersionCatalogBatch = Omit<VersionBatch, 'scenarios'> & { scenarios: VersionSnapshotSummary[] };
export interface VersionComparisonSummary {
  id: string; current_batch: VersionComparison['current_batch']; previous_batch: VersionComparison['previous_batch'];
  current: VersionSnapshotSummary; previous: VersionSnapshotSummary | null;
  deltas: VersionComparison['deltas'];
}
export interface ThreadDerived {cpu_pct: number | null; cpu_us_per_io: number | null; ipc: number | null; instructions_per_io: number | null; cycles_per_io: number | null}
export interface VersionCandidate {
  id: string; kind: string; title: string; priority: 'high' | 'medium';
  evidence: string[]; missing: string[]; validation: string[];
  verdict: VersionVerdict; note: string;
  review_history: {verdict: string; note: string; reviewer: string; timestamp: string}[];
}
export interface VersionAnalysis {
  id: string; created_at: string; framework: 'LangGraph';
  steps: {id: string; title: string; status: string}[];
  summary: string; candidates: VersionCandidate[];
  events: {title: string; detail: string; timestamp: string}[];
}
export interface VersionComparison {
  id: string; source: VersionSource; environment: VersionBatch['environment'];
  current_batch: Pick<VersionBatch,'id' | 'title' | 'build' | 'created_at' | 'changes'>;
  previous_batch: Pick<VersionBatch,'id' | 'title' | 'build' | 'created_at' | 'changes'> | null;
  current: VersionSnapshot; previous: VersionSnapshot | null;
  deltas: {iops_pct: number | null; latency_pct: number | null; p99_pct: number | null};
  threads: {key: string; before: ThreadCapture | null; after: ThreadCapture | null; derived_before: ThreadDerived | null; derived_after: ThreadDerived | null}[];
  cpus: {key: string; before: CpuCapture | null; after: CpuCapture | null}[];
  network: {key: string; before: NetworkCapture | null; after: NetworkCapture | null}[];
  quality: string[]; analysis: VersionAnalysis | null;
}
