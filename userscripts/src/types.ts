/** Mirrors Python FetchJobStatus enum. */
export type JobStatus = 'pending' | 'assigned' | 'completed' | 'failed' | 'skipped';

export interface JobContext {
  university_name: string;
  agent_state: string;
  intent: string;
  parent_url: string;
  depth: number;
  org_unit_name: string;
  hints: string[];
}

export interface FetchJob {
  id: string;
  url: string;
  status: JobStatus;
  context: JobContext;
  created_at: string;
  timeout_seconds: number;
  action?: FetchAction | null;
  identity_url?: string | null;
}

export interface FetchAction {
  kind: string;
  form_name?: string;
  fields?: Record<string, string>;
  submit?: boolean;
  synthetic_url?: string;
  label?: string;
  page_index?: number;
  state_id?: string;
}

export interface PaginationState {
  kind: 'form_submit';
  state_id: string;
  label: string;
  page_index: number;
  total_pages?: number;
  form_name: string;
  fields: Record<string, string>;
  submit: boolean;
  synthetic_url: string;
  url: string;
}

export interface CompleteResponse {
  status: string;
  next_job?: FetchJob;
}

export interface QueueStats {
  pending: number;
  assigned: number;
  completed: number;
  failed: number;
  skipped: number;
}

export interface FrontendHealth {
  alive: boolean;
  last_seen_seconds_ago: number | null;
  owner_tab_id: string | null;
  url: string | null;
  current_job_id: string | null;
  auto_mode: boolean;
  paused: boolean;
  client_timestamp_ms: number | null;
  stale_after_seconds: number;
}

export interface HeartbeatPayload {
  owner_tab_id: string;
  url: string;
  current_job_id?: string | null;
  auto_mode: boolean;
  paused: boolean;
  timestamp: number;
}

export interface PendingDecision {
  id: string;
  kind: string;
  org_unit_name: string;
  failure_count: number;
  sample_urls: string[];
  suggested_action: string;
  status: string;
  action?: string | null;
  created_at: string;
  resolved_at?: string | null;
}

export interface StatusResponse {
  queue: QueueStats;
  current_job?: FetchJob;
  pending_decision?: PendingDecision;
  frontend_health?: FrontendHealth;
  agent?: Record<string, unknown>;
  server_uptime_seconds: number;
}
