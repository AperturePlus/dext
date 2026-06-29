/** Pure SW-side PanelState projector (amend §7). Computes the PanelState for a
 *  given sender so REGISTER/TICK receipts and STATE_CHANGED pushes carry a
 *  sender-correct view: isBoundTab is true only for the bound tab; `bound`
 *  reflects whether ANY tab is bound (so a non-bound tab knows it cannot take
 *  over). lastError is the RAW ControllerError — the content panel (panelState.ts
 *  /formatError) renders the human text; the SW never localizes. No chrome, no DOM.
 *
 *  ControllerState.pendingDecision was added in Task 5 (slice 5); this projector
 *  now reads it directly off the state object. */

import type { ControllerState } from '../shared/state.js';
import type { PanelState } from '../shared/rpc.js';

export interface PanelSender {
  tabId: number | null;
}

export function buildPanelState(state: ControllerState, sender: PanelSender): PanelState {
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
    pendingDecision: state.pendingDecision,
  };
}
