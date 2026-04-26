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
