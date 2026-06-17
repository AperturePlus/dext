import * as api from './api';
import { state } from './state';

const HEARTBEAT_INTERVAL = 2000;

const ownerTabId = (() => {
  try {
    if (typeof crypto?.randomUUID === 'function') {
      return crypto.randomUUID();
    }
  } catch {
    // ignore
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2)}`;
})();

let heartbeatTimer: ReturnType<typeof setInterval> | null = null;
let heartbeatInFlight = false;

async function sendHeartbeat(): Promise<void> {
  if (state.instanceRole !== 'owner' || heartbeatInFlight) return;
  heartbeatInFlight = true;
  try {
    await api.sendHeartbeat({
      owner_tab_id: ownerTabId,
      url: window.location.href,
      current_job_id: state.currentJob?.id ?? null,
      auto_mode: state.autoMode,
      paused: state.paused,
      timestamp: Date.now(),
    });
  } catch {
    // Backend connectivity is already reflected by normal status polling.
  }
  heartbeatInFlight = false;
}

export function startHeartbeat(): void {
  if (heartbeatTimer !== null) return;
  void sendHeartbeat();
  heartbeatTimer = setInterval(() => {
    void sendHeartbeat();
  }, HEARTBEAT_INTERVAL);
}

export function stopHeartbeat(): void {
  if (heartbeatTimer !== null) {
    clearInterval(heartbeatTimer);
    heartbeatTimer = null;
  }
}
