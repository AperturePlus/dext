/** Exponential backoff for /status retries across SW restarts (spec §2.3 step 2).
 *  Pure + deterministic so it is unit-testable without a clock. delay(count) =
 *  min(BASE * 2^(count-1), CAP); count <= 0 → 0 (no backoff, retry immediately).
 *  Count is the number of consecutive backend failures; the controller bumps it
 *  before computing the next gate, so the first failure (count=1) waits BASE. */

const BASE_MS = 2000;
const CAP_MS = 60_000;

export function computeBackoffMs(failureCount: number): number {
  if (failureCount <= 0) return 0;
  return Math.min(BASE_MS * 2 ** (failureCount - 1), CAP_MS);
}

export function nextRetryAt(failureCount: number, now: number): number {
  return now + computeBackoffMs(failureCount);
}
