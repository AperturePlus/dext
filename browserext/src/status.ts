/** Pure navigation classifier — the ONLY place status-code → outcome mapping lives.
 * No chrome, no IO. Mirrors the backend's retry.classify_fetch_failure intent
 * (404/410 = terminal skip, 5xx = retryable gateway, 429 = rate-limited). */

export type NavOutcome = 'ok' | 'not_found' | 'gateway' | 'rate_limited' | 'nav_error';

export type NavInput =
  | { kind: 'completed'; statusCode: number }
  | { kind: 'error'; error: string };

export const DEAD_STATUSES = new Set([404, 410]);
export const RATE_LIMITED_STATUS = 429;
export const GATEWAY_STATUSES = new Set([502, 503, 504]);

export function classifyNavigation(input: NavInput): NavOutcome {
  if (input.kind === 'error') return 'nav_error';
  const code = input.statusCode;
  if (DEAD_STATUSES.has(code)) return 'not_found';
  if (code === RATE_LIMITED_STATUS) return 'rate_limited';
  if (GATEWAY_STATUSES.has(code)) return 'gateway';
  return 'ok';
}
