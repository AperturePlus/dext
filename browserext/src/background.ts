/** MV3 background service worker entry. Composition root only — no logic.
 * Constructs the real chrome.* runtime, the fetch HTTP client, the chrome.storage
 * storage, then wires navMonitor + watchdog and starts both. */

import { createRealChromeRuntime } from './chrome';
import type { ChromeRuntime } from './chrome';
import { createFetchApi } from './api';
import type { ApiClient } from './api';
import { createChromeStorage } from './storage';
import type { Storage } from './storage';
import { createNavMonitor } from './navMonitor';
import type { NavMonitor } from './navMonitor';
import { createWatchdog } from './watchdog';
import type { Watchdog } from './watchdog';

const API_BASE = 'http://127.0.0.1:21520/api';

export interface WireDeps {
  chrome: ChromeRuntime;
  api: ApiClient;
  storage: Storage;
}

export function wireBackground(deps: WireDeps): { navMonitor: NavMonitor; watchdog: Watchdog } {
  const navMonitor = createNavMonitor(deps);
  const watchdog = createWatchdog(deps);
  navMonitor.start();
  watchdog.start();
  return { navMonitor, watchdog };
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
