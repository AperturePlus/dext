/** Pure SW-side PanelState projector (amend §7). Computes the PanelState for a
 *  given sender so REGISTER/TICK receipts and STATE_CHANGED pushes carry a
 *  sender-correct view: isBoundTab is true only for the bound tab; `bound`
 *  reflects whether ANY tab is bound (so a non-bound tab knows it cannot take
 *  over). lastError is the RAW ControllerError — the content panel (panelState.ts
 *  /formatError) renders the human text; the SW never localizes. No chrome, no DOM.
 *
 *  Note: ControllerState.pendingDecision is added in Task 5; until then this
 *  projector reads it via a safe cast rather than mutating shared/state.ts. */

import type { ControllerState } from '../shared/state.js';
import type { PanelState } from '../shared/rpc.js';
import type { PendingDecision } from '../shared/types.js';

export interface PanelSender {
  tabId: number | null;
}

/** ControllerState will gain `pendingDecision` in Task 5; until then access it
 *  through this widened view so the projector compiles under strict tsc without
 *  touching shared/state.ts in this task. */
type ControllerStateWithDecision = ControllerState & { pendingDecision: PendingDecision | null };

export function buildPanelState(state: ControllerState, sender: PanelSender): PanelState {
  const s = state as ControllerStateWithDecision;
  return {
    isBoundTab: sender.tabId !== null && sender.tabId === state.boundTabId,
    bound: state.boundTabId !== null,
    connected: state.connected,
    autoMode: state.autoMode,
    paused: state.paused,
    phase: state.phase,
    currentJob: state.currentJob,
    navigationAttempt: state.navigation?.attempt ?? 0,
    lastError: state.lastError,
    pendingDecision: s.pendingDecision,
  };
}
