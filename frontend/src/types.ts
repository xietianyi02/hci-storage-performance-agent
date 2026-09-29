export type Page = 'version' | 'poc' | 'overview' | 'cases' | 'knowledge' | 'workflows' | 'tools';
export type Verdict = 'pending' | 'confirmed' | 'rejected';
export type WorkflowId = 'version' | 'poc' | 'research';
export interface Stage { id: string; title: string; status: string; summary: string }
export interface Fio { rw: string; bs: string; iodepth: number; numjobs: number; vm_count: number }
export interface Metric { iops: number; bandwidth_mib: number; latency_ms: number }
export interface Evidence { id: string; title: string; summary: string; source: string; type: string; stage_id: string; data?: unknown }
export interface Recommendation { id: string; title: string; reason: string; action: string; expected_effect: string; risk: string; verdict: Verdict; feedback_note?: string; evidence_ids?: string[]; feedback_history?: {verdict:Verdict;note:string;reviewer:string;timestamp:string;source:string}[] }
export interface Experiment { id: string; title: string; status: string; source: string; before?: Metric; after?: Metric; change_pct?: {iops: number; latency_ms: number}; recommendation_ids?: string[]; procedure: string | string[] }
export interface Case {
  id: string; title: string; workflow_id: WorkflowId; protocol: string; description: string; owner?: string;
  status: string; updated_at?: string; created_at?: string; baseline_version?: string; target_version?: string;
  scenario?: { layout: string; local_host: string; remote_host: string; nodes: number; fio: Fio; business_entry: string };
  fio?: Fio; stages?: Stage[]; events?: {id: string; run_id?: string | null; stage_id: string; title: string; detail: string; timestamp: string; kind: string; source: string}[];
  evidence?: Evidence[]; hypotheses?: {id: string; title: string; reason: string; confidence: number | string; evidence_ids: string[]; status: string}[];
  recommendations?: Recommendation[]; experiments?: Experiment[];
  metrics?: { baseline: Metric; current: Metric; source: string };
  report?: { summary: string; conclusion: string; limitations: string | string[] }; run_id?: string; data_source?: string; mode?: string;
  analysis?: {provider?:string;mode?:string;explanation?:string;delta_pct?:unknown;[key:string]:unknown};
}
export interface Workflow {id: WorkflowId; title: string; description: string; focus: string | string[]; stages?: Stage[]}
export interface Knowledge {id: string; title: string; category?: string; summary?: string; content?: string; source?: string; status?: string; tags?: string[]; [key: string]: unknown}
export interface Tool {id: string; name?: string; title?: string; description?: string; category?: string; mode?: string; status?: string; risk?: string; [key: string]: unknown}
export interface Overview { total_cases?: number; awaiting_approval?: number; completed_cases?: number; recommendations?: {total: number; confirmed: number; rejected: number; pending: number; accuracy_pct: number | null}; by_workflow?: {id: string; title: string; count: number; completed: number}[]; recent_cases?: Case[]; [key: string]: unknown }
