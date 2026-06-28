/** Pure two-level navigation deadline (amend §2.4). NO chrome, no IO. The
 *  Controller calls navTimeoutKind(state, now) in the tick and acts:
 *    never_landed        → navigation retry funnel (only a navigate kind may re-navigate)
 *    content_unavailable → NEVER refresh; set lastError content_unavailable{missing}, phase 'error',
 *                        recoveryExhausted=true (PAGE_READY/HTTP are not RPC-recoverable; slice 4 adds
 *                        RPC recovery for capture/prepare/perform only)
 *    none                → keep observing
 *
 *  Two deadlines:
 *    navigationDeadlineAt    = issuedAt + 30s   (no commit yet) → never_landed funnel
 *    landingSignalsDeadlineAt = committedAt + 30s (commit present, PAGE_READY/HTTP missing) → content_unavailable
 *
 *  page_ready is checked before http_outcome when both are missing (PAGE_READY is the
 *  stronger signal — if the page never reported ready, that is the root cause). */

import type { ControllerState } from '../shared/state.js';

export const NAV_DEADLINE_MS = 30_000;
export const LANDING_SIGNALS_DEADLINE_MS = 30_000;

export type DeadlineVerdict =
  | { kind: 'none' }
  | { kind: 'never_landed' }
  | {
      kind: 'content_unavailable';
      missing: 'page_ready' | 'http_outcome';
      sourceDocumentId: string;
    };

export function navTimeoutKind(state: ControllerState, now: number): DeadlineVerdict {
  const nav = state.navigation;
  if (!nav) return { kind: 'none' };

  if (!nav.commit) {
    // No commit yet → navigation deadline (only a navigate may enter the funnel).
    if (now >= nav.issuedAt + NAV_DEADLINE_MS) return { kind: 'never_landed' };
    return { kind: 'none' };
  }

  // Commit present → landing-signals deadline. Never refresh.
  if (now < nav.commit.committedAt + LANDING_SIGNALS_DEADLINE_MS) return { kind: 'none' };

  const pageReadyMissing = !nav.pageReady || nav.pageReady.documentId !== nav.commit.documentId;
  if (pageReadyMissing) {
    return { kind: 'content_unavailable', missing: 'page_ready', sourceDocumentId: nav.commit.documentId };
  }
  const httpMissing = !nav.http
    || nav.http.requestId !== nav.requestId
    || (nav.http.documentId !== undefined && nav.http.documentId !== nav.commit.documentId);
  if (httpMissing) {
    return { kind: 'content_unavailable', missing: 'http_outcome', sourceDocumentId: nav.commit.documentId };
  }
  return { kind: 'none' };
}
