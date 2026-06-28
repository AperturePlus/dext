/** ControllerState and its sub-shapes — the single chrome.storage.local
 * payload a CrawlController rehydrates from across SW restarts. Shape follows
 * the amendment (§1.1–§1.3) exactly: PendingRpc carries delivery + sourceDocumentId;
 * NavigationState carries requestId/commit/http/pageReady/action slots;
 * lastError is a discriminated ControllerError, never a string. Slice 1 only
 * persists the binding skeleton; later slices fill navigation/pendingRpc. */

import type { FetchJob } from './types.js';

export type ControllerPhase =
  | 'idle' | 'assigned' | 'claiming' | 'navigating' | 'landed'
  | 'acting' | 'capturing' | 'submitting' | 'error';

export type RpcOperation = 'prepare_action' | 'perform_action' | 'capture';

export interface PendingRpc {
  id: string;
  jobId: string;
  op: RpcOperation;
  sourceDocumentId: string;
  delivery: 'prepared' | 'received';
  issuedAt: number;
  resultDeadlineAt: number;
}

export type TerminalUnavailableReason = 'not_found' | 'content_removed' | 'empty_page' | string;

export interface PageDetection {
  errorPage: boolean;
  terminalReason: TerminalUnavailableReason | null;
}

export type NavOutcome =
  | 'ok' | 'not_found' | 'gateway' | 'rate_limited' | 'nav_error' | 'unexpected_status';

export interface NavigationState {
  jobId: string;
  requestedUrl: string;
  issuedAt: number;
  attempt: number;
  kind: 'navigate' | 'form_action';
  sourceDocumentId?: string;
  requestId?: string;
  commit?: {
    documentId: string;
    committedUrl: string;
    committedAt: number;
  };
  http?: {
    requestId: string;
    documentId?: string;
    statusCode?: number;
    outcome: NavOutcome;
    error?: string;
  };
  pageReady?: {
    documentId: string;
    url: string;
    detection: PageDetection;
  };
  action?: {
    expectedEffect: 'new_document' | 'same_document' | 'unknown';
    method: string;
    preparationFingerprint: string;
    invocationReported: boolean;
    effectConfirmed: boolean;
  };
  acceptedUrl?: string;
}

export type ControllerError =
  | {
      kind: 'content_unavailable';
      missing:
        | 'page_ready' | 'capture_result' | 'action_prepare'
        | 'action_result' | 'http_outcome';
      sourceDocumentId: string;
      since: number;
      recoveryAttempts: number;
      nextRecoveryAt: number | null;
      recoveryExhausted: boolean;
    }
  | { kind: 'nav_error'; error: string }
  | { kind: 'gateway_5xx' }
  | { kind: 'unexpected_status'; statusCode: number }
  | { kind: 'rate_limited' };

export interface ControllerState {
  boundTabId: number | null;
  boundAt: number | null;
  connected: boolean;
  autoMode: boolean;
  paused: boolean;
  currentJob: FetchJob | null;
  phase: ControllerPhase;
  phaseStartedAt: number;
  navigation: NavigationState | null;
  pendingRpc: PendingRpc | null;
  backendFailureCount: number;
  nextBackendRetryAt: number | null;
  lastError: ControllerError | null;
}

export function initialControllerState(now: number): ControllerState {
  return {
    boundTabId: null,
    boundAt: null,
    connected: false,
    autoMode: false,
    paused: false,
    currentJob: null,
    phase: 'idle',
    phaseStartedAt: now,
    navigation: null,
    pendingRpc: null,
    backendFailureCount: 0,
    nextBackendRetryAt: null,
    lastError: null,
  };
}
