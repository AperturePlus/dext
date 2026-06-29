/** CS↔SW RPC contract (spec §4.2, amend §7). Shared by both tsconfigs;
 *  imports only types, never DOM or chrome. PanelState.lastError is
 *  ControllerError | null (amend §7) — the content panel renders the
 *  human-readable text, not the SW. */

import type { ControllerError, ControllerPhase, TerminalUnavailableReason } from './state.js';
import type { FetchAction, FetchJob, PaginationState, PendingDecision } from './types.js';

export type PanelCommand =
  | { kind: 'bind' }
  | { kind: 'unbind' }
  | { kind: 'set_auto'; value: boolean }
  | { kind: 'set_paused'; value: boolean }
  | { kind: 'open' }
  | { kind: 'submit' }
  | { kind: 'skip'; reason?: string }
  | { kind: 'fail'; message?: string }
  | { kind: 'override'; url: string }
  | { kind: 'decision'; id: string; action: string };

export type CsToSw =
  | { op: 'REGISTER'; url: string }
  | { op: 'TICK' }
  | {
      op: 'PAGE_READY';
      url: string;
      title: string;
      detection: { errorPage: boolean; terminalReason: TerminalUnavailableReason | null };
    }
  | {
      op: 'CAPTURE_RESULT';
      rpcId: string; jobId: string; ok: boolean; url: string;
      html?: string; title?: string; paginationStates?: PaginationState[];
      detection?: { errorPage: boolean; terminalReason: TerminalUnavailableReason | null };
      error?: string;
    }
  | {
      op: 'ACTION_RESULT';
      rpcId: string; jobId: string; ok: boolean;
      invoked: boolean; navigationExpected: boolean; effectApplied: boolean;
      error?: string;
    }
  | { op: 'COMMAND'; command: PanelCommand };

export type SwToCs =
  | { op: 'STATE_CHANGED'; state: PanelState }
  | { op: 'CAPTURE'; rpcId: string; jobId: string }
  | {
      op: 'PREPARE_ACTION';
      rpcId: string; jobId: string; action: FetchAction;
    }
  | {
      op: 'PERFORM_ACTION';
      rpcId: string; jobId: string; action: FetchAction;
      preparationFingerprint: string;
    }
  | { op: 'TOAST'; message: string; kind?: 'info' | 'error' };

export interface PanelState {
  isBoundTab: boolean;
  bound: boolean;
  connected: boolean;
  autoMode: boolean;
  paused: boolean;
  phase: ControllerPhase;
  currentJob: FetchJob | null;
  navigationAttempt: number;
  lastError: ControllerError | null;
  pendingDecision: PendingDecision | null;
}

export interface MessageReceipt {
  received: true;
  state?: PanelState;
}

// Named aliases over the CsToSw result variants (amend §4.1, §5.1, §5.3). The CS
// rpc ledger (shared/rpcLedger.ts) and the Controller import these so a result
// type is a single name rather than an Extract<...> at every call site. The
// ACTION_PREPARED variant is added in slice-4 Task 6 if still absent.
export type CaptureResult = Extract<CsToSw, { op: 'CAPTURE_RESULT' }>;
export type ActionResult = Extract<CsToSw, { op: 'ACTION_RESULT' }>;
export type ActionPrepared = Extract<CsToSw, { op: 'ACTION_PREPARED' }>;
