/** Pure /status.current_job → ControllerState reconciliation (spec §2.3 step 1,
 *  §2.5 rehydration, amend §6.3 non-RPC rows). Mutates `state` in place.
 *
 *  - Backend released the slot (currentJob null): if we held a cached job, clear
 *    it + any stale navigation/pendingRpc/lastError and go idle.
 *  - New/different job id: cache the job, DISCARD stale navigation/pendingRpc/
 *    lastError (a previous attempt's signals must not leak onto a new job), and
 *    go `assigned` (have a job but not navigating yet — slice 3 claims; an
 *    unbound controller stays assigned waiting for a bind).
 *  - Same job id: NO mutation — preserve in-flight navigation/pendingRpc so a
 *    reconcile mid-attempt does not clobber current signals.
 *
 *  This function owns ONLY the currentJob/navigation/pendingRpc/lastError/phase
 *  fields. `connected`, `backendFailureCount`, `nextBackendRetryAt`,
 *  `boundTabId`, `autoMode`, `paused` are the controller's (or bind's) concern. */

import type { ControllerState } from '../shared/state.js';
import type { FetchJob } from '../shared/types.js';

export function applyReconcile(state: ControllerState, currentJob: FetchJob | null, now: number): void {
  if (currentJob === null) {
    if (state.currentJob !== null) {
      state.currentJob = null;
      state.navigation = null;
      state.pendingRpc = null;
      state.lastError = null;
      state.phase = 'idle';
      state.phaseStartedAt = now;
    }
    return;
  }
  if (state.currentJob === null || state.currentJob.id !== currentJob.id) {
    state.currentJob = currentJob;
    state.navigation = null;
    state.pendingRpc = null;
    state.lastError = null;
    state.phase = 'assigned';
    state.phaseStartedAt = now;
  }
  // same job id → leave in-flight signals untouched
}
