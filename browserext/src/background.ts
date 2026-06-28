/** MV3 background service worker entry. Composition root only — no logic.
 *  Constructs the real chrome.* runtime, the fetch HTTP client, the
 *  chrome.storage ControllerState area, then wires the gated CrawlController
 *  and the thin navMonitor adapter (slice 3: navMonitor forwards raw main-frame
 *  events + outcome to the controller; it no longer holds api/storage and never
 *  fails/skips/navigates on its own). The 1-minute reconciliation alarm drives
 *  controller.tick(). Gate OFF in official slice 1–5 builds → all of this is a
 *  runtime no-op. */

import { createRealChromeRuntime } from './chrome.js';
import type { ChromeRuntime } from './chrome.js';
import { createFetchApi } from './api.js';
import type { ApiClient } from './api.js';
import { createNavMonitor } from './navMonitor.js';
import type { NavMonitor } from './navMonitor.js';
import { createCrawlController, createControllerStorage } from './controller/controller.js';
import type { CrawlController, StorageArea } from './controller/controller.js';
import {
  RECONCILIATION_ALARM_NAME,
  RECONCILIATION_PERIOD_MINUTES,
} from './controller/controller.js';

const API_BASE = 'http://127.0.0.1:21520/api';

export interface WireDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
}

export function wireBackground(deps: WireDeps): {
  navMonitor: NavMonitor;
  controller: CrawlController;
} {
  const controller = createCrawlController({
    storage: createControllerStorage(chrome.storage.local as unknown as StorageArea),
    api: deps.api,
    chrome: deps.chrome,
  });
  const navMonitor = createNavMonitor({ chrome: deps.chrome, controller });
  navMonitor.start();
  deps.chrome.registerAlarm(RECONCILIATION_ALARM_NAME, RECONCILIATION_PERIOD_MINUTES, () => {
    void controller.tick();
  });
  return { navMonitor, controller };
}

// Self-invoke on SW startup with real implementations, but skip in node tests where
// chrome is undefined. The typeof check keeps both tsc and the test harness happy.
if (typeof chrome !== 'undefined' && chrome.storage?.local) {
  wireBackground({
    chrome: createRealChromeRuntime(),
    api: createFetchApi(API_BASE),
  });
}
