/** Pure navigation classifier — the ONLY place status-code → outcome mapping
 *  lives (spec §1.4, amend §3.1). No chrome, no IO. The canonical NavOutcome
 *  (6 values incl. unexpected_status) lives in shared/state.ts; this module
 *  imports it rather than redefining it.
 *
 *  Algorithm (amend §3.1):
 *    error kind                     → 'nav_error'
 *    2xx (except 204/205) or 304     → 'ok'
 *    404/410                         → 'not_found'
 *    429                             → 'rate_limited'
 *    500/502/503/504                 → 'gateway'
 *    anything else                   → 'unexpected_status'   (incl. 204/205/401/403; 301/302 defensively)
 *
 *  onBeforeRedirect is NEVER classified (amend §3.1): the Controller runs the
 *  same-crawl-site gate on redirectUrl directly, so 3xx never reaches here in
 *  production. The 301/302 → unexpected_status mapping is the defensive
 *  behavior if a 3xx somehow arrives via onCompleted. */

import type { NavOutcome } from './shared/state.js';
import type { NavInput } from './shared/navEvents.js';

export type { NavOutcome, NavInput };

export const DEAD_STATUSES = new Set([404, 410]);
export const RATE_LIMITED_STATUS = 429;
export const GATEWAY_STATUSES = new Set([500, 502, 503, 504]);

export function classifyNavigation(input: NavInput): NavOutcome {
  if (input.kind === 'error') return 'nav_error';
  const code = input.statusCode;
  if ((code >= 200 && code < 300 && code !== 204 && code !== 205) || code === 304) {
    return 'ok';
  }
  if (DEAD_STATUSES.has(code)) return 'not_found';
  if (code === RATE_LIMITED_STATUS) return 'rate_limited';
  if (GATEWAY_STATUSES.has(code)) return 'gateway';
  return 'unexpected_status';
}
