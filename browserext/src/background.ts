/** MV3 background service worker entry. Composition root only — no logic.
 * Constructs the real chrome.* runtime, the fetch HTTP client, the chrome.storage
 * storage, then wires navMonitor (Phase-1 thin adapter; refactored in slice 3) and
 * the gated CrawlController. The Phase-1 watchdog was DELETED in slice 2 — the
 * slice-2 reconciliation alarm (spec §2.6) drives controller.tick() and replaces it;
 * that alarm wiring lands once the controller exposes its alarm constants. navMonitor
 * keeps running throughout. */

import { createRealChromeRuntime } from './chrome.js';
import type { ChromeRuntime } from './chrome.js';
import { createFetchApi } from './api.js';
import type { ApiClient } from './api.js';
import { createChromeStorage } from './storage.js';
import type { Storage } from './storage.js';
import { createNavMonitor } from './navMonitor.js';
import type { NavMonitor } from './navMonitor.js';
import { createCrawlController, createControllerStorage } from './controller/controller.js';
import type { CrawlController, StorageArea } from './controller/controller.js';

const API_BASE = 'http://127.0.0.1:21520/api';

export interface WireDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export function wireBackground(deps: WireDeps): {
  navMonitor: NavMonitor;
  controller: CrawlController;
} {
  const navMonitor = createNavMonitor(deps);
  // Controller rehydrates from chrome.storage.local across SW restarts. In slice 1/2
  // its tick/bind are gated no-ops (EXCLUSIVE_CONTROL_ENABLED=false in the official
  // build); constructed here so wiring + persistence are exercised. api + chrome let
  // the gated-ON tick do /status reconcile + heartbeat (slice 2). The slice-2
  // reconciliation alarm (replacing the deleted watchdog) drives controller.tick().
  const controller = createCrawlController({
    storage: createControllerStorage(chrome.storage.local as unknown as StorageArea),
    api: deps.api,
    chrome: deps.chrome,
  });
  navMonitor.start();
  return { navMonitor, controller };
}

// Self-invoke on SW startup with real implementations, but skip in node tests where
// chrome is undefined. The typeof check keeps both tsc and the test harness happy.
if (typeof chrome !== 'undefined' && chrome.storage?.local) {
  wireBackground({
    chrome: createRealChromeRuntime(),
    api: createFetchApi(API_BASE),
    storage: createChromeStorage(chrome.storage.local),
  });
}
