export interface TraceEvent {
  event_id: string;
  parent_event_id: string | null;
  run_id: string;
  node: string;
  depth: number;
  timestamp: string;
  duration_ms: number | null;
  agent_id: string | null;
  action: string | null;
  intent: string | null;
  decision: any | null;
  cost_delta: number;
  cumulative_cost: number;
  status: string;
  metadata: any;
  llm_token_counts: Record<string, number> | null;
  latency_breakdown: Record<string, number> | null;
}

export interface PolicyResponse {
  policy_version: string;
  content: Record<string, any>;
}

export interface PolicyReloadResponse {
  reloaded: boolean;
  policy_version: string;
  rules_count: number;
}

export interface TraceResponse {
  schema_version: string;
  run_id: string;
  task: string;
  started_at: string;
  finished_at: string;
  total_duration_ms: number | null;
  total_cost_usd: number;
  final_status: string;
  final_answer: string;
  policy_version: string;
  llm_providers: Record<string, string>;
  events: TraceEvent[];
  edges: { from: string; to: string }[];
}
