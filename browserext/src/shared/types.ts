/** DTOs mirroring userscripts/src/types.ts and the backend types.ts (dext.types).
 *  The extension vendors its own copy so its build is independent. */

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
