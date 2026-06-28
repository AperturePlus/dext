/** Owner-tab heartbeat, now owned by the extension (the userscript stands down
 *  under the data-dext-extension-controller marker, so it no longer heartbeats;
 *  spec §4.6 "content script does not call backend" + §2.4 "paused does not stop
 *  heartbeat"). POSTs /heartbeat every tick when bound.
 *
 *  `ownerTabId` is DETERMINISTIC — `dext-ext-${boundTabId}-${boundAt}` — so it is
 *  stable across MV3 SW restarts (both fields are persisted in ControllerState).
 *  The backend uses owner_tab_id only as a label (is_alive is time-based,
 *  health.py), so a deterministic string is correct and avoids a separate
 *  storage key + crypto. A re-bind changes boundAt → new owner id (new session). */

import type { ApiClient, HeartbeatPayload } from '../api.js';
import type { ControllerState } from '../shared/state.js';

export function ownerTabIdFor(state: ControllerState): string | null {
  if (state.boundTabId === null || state.boundAt === null) return null;
  return `dext-ext-${state.boundTabId}-${state.boundAt}`;
}

export interface Heartbeat {
  send(state: ControllerState, now: number): Promise<void>;
}

export function createHeartbeat(api: Pick<ApiClient, 'sendHeartbeat'>): Heartbeat {
  return {
    async send(state, now) {
      const ownerTabId = ownerTabIdFor(state);
      if (ownerTabId === null) return;            // unbound → no owner to heartbeat
      const payload: HeartbeatPayload = {
        owner_tab_id: ownerTabId,
        url: state.currentJob?.url ?? '',
        current_job_id: state.currentJob?.id ?? null,
        auto_mode: state.autoMode,
        paused: state.paused,
        timestamp: now,
      };
      await api.sendHeartbeat(payload);           // api.sendHeartbeat swallows errors
    },
  };
}
