/** Watchdog: on each alarm, read /status; if the owner heartbeat is stale beyond the
 * threshold AND a current_job exists, re-redirect the owner tab to the job URL (the
 * userscript can't recover itself from about:neterror). Debounced per-job via storage.
 * Does NOT call fail/skip — that's navMonitor's job. Closed loop (spec §3): redirect →
 * nav counts → navMonitor fails after MAX_ABNORMAL_NAVS. */

import type { ApiClient } from './api.js';
import type { ChromeRuntime } from './chrome.js';
import type { Storage } from './storage.js';

export const STALE_THRESHOLD_SECONDS = 15;
export const WATCHDOG_ALARM_NAME = 'dext-watchdog';
export const WATCHDOG_PERIOD_MINUTES = 1;

export interface WatchdogDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export interface Watchdog {
  start(): void;
  tick(): Promise<void>;
}

export function createWatchdog(deps: WatchdogDeps): Watchdog {
  const { chrome, api, storage } = deps;

  async function tick(): Promise<void> {
    const status = await api.getStatus();
    if (!status) return;
    const fh = status.frontend_health;
    if (fh.alive) return;
    const lastSeen = fh.last_seen_seconds_ago;
    if (lastSeen === null || lastSeen < STALE_THRESHOLD_SECONDS) return;
    const job = status.current_job;
    if (!job) return; // backend already released the slot
    if (!(await storage.shouldRedirect(job.id))) return; // debounced
    const tabId = await chrome.findOwnerTab();
    if (tabId === null) return; // no candidate tab; try next alarm
    await chrome.updateTabUrl(tabId, job.url);
    await storage.recordRedirect(job.id);
  }

  return {
    start() {
      chrome.registerWatchdogAlarm(WATCHDOG_ALARM_NAME, WATCHDOG_PERIOD_MINUTES, () => { void tick(); });
    },
    tick,
  };
}
