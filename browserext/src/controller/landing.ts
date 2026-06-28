/** Pure landing evaluation — the amend §2.3 five-condition rule. Reads ONLY
 *  state.navigation (+ now, reserved); mutates nothing. The Controller calls
 *  this after each signal arrives and acts on the verdict:
 *    landed        → phase 'landed', set acceptedUrl (slice 4 then dispatches capture)
 *    terminal_skip → skip (no budget)
 *    error_retry   → retry funnel (amend §3.2 table)
 *    waiting       → keep observing
 *
 *  Five conditions (amend §2.3): (1) correlated main-frame commit, phase navigating|acting;
 *  (2) acceptable HTTP outcome from the SAME requestId final onCompleted, and if the HTTP
 *  event carried a documentId it must equal commit.documentId; (3) committedUrl passes the
 *  same-crawl-site gate; (4) PAGE_READY whose documentId equals commit.documentId; (5)
 *  PAGE_READY.detection errorPage===false && terminalReason===null.
 *
 *  requestId/documentId/job/time/phase correlation (binding, join) is established by the
 *  Controller's deliver* handlers BEFORE landing is evaluated; this function only checks
 *  whether the already-correlated signals satisfy the five conditions. */

import type { ControllerState, NavOutcome } from '../shared/state.js';
import { isWechatHost, sameCrawlSite } from '../shared/urlGate.js';

export type LandingVerdict =
  | { kind: 'landed'; acceptedUrl: string; documentId: string }
  | {
      kind: 'terminal_skip';
      reason: 'not_found' | 'content_removed' | 'empty_page' | 'wechat_redirect' | 'offsite_redirect';
    }
  | { kind: 'error_retry'; outcome: Exclude<NavOutcome, 'ok'>; detail?: string }
  | { kind: 'waiting' };

const TERMINAL_REASON_TO_SKIP = {
  not_found: 'not_found',
  content_removed: 'content_removed',
  empty_page: 'empty_page',
} as const;

function isWechatHostUrl(url: string): boolean {
  try {
    return isWechatHost(new URL(url).hostname);
  } catch {
    return false;
  }
}

export function evaluateLanding(state: ControllerState, _now: number): LandingVerdict {
  const nav = state.navigation;
  if (!nav || !nav.commit) return { kind: 'waiting' };

  // (3) same-crawl-site gate on the committed URL.
  if (!sameCrawlSite(nav.commit.committedUrl, nav.requestedUrl)) {
    return {
      kind: 'terminal_skip',
      reason: isWechatHostUrl(nav.commit.committedUrl) ? 'wechat_redirect' : 'offsite_redirect',
    };
  }

  // (4)+(5) PAGE_READY must match the commit documentId and be clean — but a stale
  // PAGE_READY (wrong documentId) means we are still waiting for the current document's.
  if (nav.pageReady) {
    if (nav.pageReady.documentId !== nav.commit.documentId) return { kind: 'waiting' };
    const d = nav.pageReady.detection;
    if (d.terminalReason === 'not_found' || d.terminalReason === 'content_removed') {
      return { kind: 'terminal_skip', reason: TERMINAL_REASON_TO_SKIP[d.terminalReason as 'not_found' | 'content_removed'] };
    }
    if (d.terminalReason === 'empty_page') {
      return { kind: 'terminal_skip', reason: 'empty_page' };
    }
    if (d.errorPage) {
      // amend §2.3: errorPage on a navigate enters the gateway retry budget (not a terminal skip).
      return { kind: 'error_retry', outcome: 'gateway' };
    }
  }

  // (2) HTTP outcome from the SAME requestId; if it carried a documentId it must join commit.
  if (nav.http) {
    if (nav.http.requestId !== nav.requestId) return { kind: 'waiting' };   // stale attempt's HTTP
    if (nav.http.documentId !== undefined && nav.http.documentId !== nav.commit.documentId) {
      return { kind: 'waiting' };   // join fails; await a correlated onCompleted
    }
    const outcome = nav.http.outcome;
    if (outcome !== 'ok') {
      const detail = outcome === 'unexpected_status'
        ? String(nav.http.statusCode)
        : outcome === 'nav_error'
          ? nav.http.error
          : undefined;
      return { kind: 'error_retry', outcome, detail };
    }
  }

  // (1)+(2)+(4)+(5) all satisfied and http ok.
  if (nav.pageReady && nav.http?.outcome === 'ok') {
    return {
      kind: 'landed',
      acceptedUrl: nav.commit.committedUrl,
      documentId: nav.commit.documentId,
    };
  }

  return { kind: 'waiting' };
}
