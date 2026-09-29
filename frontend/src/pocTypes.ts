export type PocSource = 'mock' | 'measured';
export type PocProtocol = 'nfs' | 'vhost' | 'iscsi';
export type PocValue = string | number | boolean;
export type PocParameters = Record<string, PocValue>;
export type PocVerdict = 'pending' | 'confirmed' | 'rejected';
export type PocMetric = 'iops' | 'bandwidth_mib_s' | 'latency_ms';
export interface PocParameterDefinition {
  id: string; label: string; component: string;
  value_type: 'integer' | 'number' | 'boolean' | 'enum' | 'string'; unit: string | null;
  protocols: PocProtocol[]; description: string; observables: string[];
  choices: PocValue[]; min: number | null; max: number | null;
  maturity: 'registered' | 'example'; adapter_key: string | null;
  device_requirements: Record<string, unknown>; fio_requirements: Record<string, unknown>;
}
export interface PocScope {
  id: string; label: string; hardware: Record<string, unknown>;
}
export interface PocGoal {
  metric: PocMetric; p99_limit_ms: number | null; min_repeats: number;
}
export interface PocMetrics {
  iops: number; bandwidth_mib_s: number; latency_ms: number; p99_ms: number | null;
  disk_latency_ms: number | null; tierd_depth: number | null;
}
export interface PocTrial {
  id: string; environment_id: string; build: string; protocol: PocProtocol;
  fio: Record<string, unknown>; source: PocSource; parameters: PocParameters;
  metrics: PocMetrics; artifacts: { kind: string; path: string; note: string }[];
  note: string; created_at: string;
}
export interface PocGroup {
  id: string; parameters: PocParameters; trial_ids: string[]; repeats: number;
  eligible: boolean; reason: string; medians: { [K in keyof PocMetrics]: number | null };
  range: Record<string, { min: number | null; max: number | null }>;
}
export interface PocRecommendation {
  group_id: string; parameters: PocParameters; goal_metric: PocMetric;
  baseline_value: number | null; observed_value: number | null; change_pct: number | null;
  verdict: PocVerdict; note: string;
  review_history: { verdict: PocVerdict; note: string; reviewer: string; timestamp: string }[];
}
export interface PocAnalysis {
  id: string; created_at: string; framework: 'LangGraph';
  steps: { id: string; title: string; status: string; detail: string }[];
  groups: PocGroup[]; baseline_group_id: string | null; best_observed_group_id: string | null;
  recommendation: PocRecommendation | null;
  correlations: { parameter_id: string; observables: string[]; summary: string; causal: false }[];
  summary: string;
}
export interface PocCampaign {
  id: string; title: string; source: PocSource; environment: PocScope;
  build: string; protocol: PocProtocol; fio: Record<string, unknown>; goal: PocGoal;
  baseline_parameters: PocParameters; parameter_definitions: PocParameterDefinition[];
  created_at: string; trials: PocTrial[]; analysis: PocAnalysis | null;
  analysis_history: PocAnalysis[];
}
