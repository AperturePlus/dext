/** Pure retry-funnel decision — the amend §3.2 navigate-kind table. NO chrome,
 *  no IO. The Controller calls this with the error_retry outcome from
 *  evaluateLanding (+ the current 1-based attempt) and acts on the verdict:
 *    skip           → POST /jobs/{id}/skip (terminal, no budget)
 *    fail           → POST /jobs/{id}/fail (rate_limited immediate; gateway/unexpected/nav_error at attempt 3)
 *    retry_navigate → bump attempt, clear requestId/commit/http/pageReady/acceptedUrl, call tabs.update again
 *
 *  Budget = 3: attempts 1 and 2 retry-navigate, attempt 3 fails. not_found and
 *  rate_limited are terminal and never consume the budget. content_unavailable
 *  is NOT a RetryOutcome — it never re-navigates (see deadline.ts). */

import type { ControllerError, NavOutcome } from '../shared/state.js';

export type RetryOutcome = Exclude<NavOutcome, 'ok'>;

export type FunnelDecision =
  | { kind: 'skip'; reason: string }
  | { kind: 'fail'; error: Exclude<ControllerError, { kind: 'content_unavailable' }> }
  | { kind: 'retry_navigate' };

const BUDGET = 3;   // attempt 3 → fail

export function funnelDecision(outcome: RetryOutcome, attempt: number, detail?: string): FunnelDecision {
  if (outcome === 'not_found') return { kind: 'skip', reason: 'not_found' };
  if (outcome === 'rate_limited') return { kind: 'fail', error: { kind: 'rate_limited' } };
  if (attempt >= BUDGET) {
    if (outcome === 'gateway') return { kind: 'fail', error: { kind: 'gateway_5xx' } };
    if (outcome === 'unexpected_status') {
      const code = Number(detail);
      return { kind: 'fail', error: { kind: 'unexpected_status', statusCode: Number.isFinite(code) ? code : 0 } };
    }
    // nav_error
    return { kind: 'fail', error: { kind: 'nav_error', error: detail ?? '' } };
  }
  return { kind: 'retry_navigate' };
}
