/** MV3 background service worker entry. Composition root only — no logic.
 * Constructs the real chrome.* runtime, the fetch HTTP client, the chrome.storage
 * storage, then wires navMonitor + watchdog and starts both. */

// Relative imports carry an explicit ".js" so the tsc output (moduleResolution:
// "bundler" preserves specifiers verbatim) resolves in the browser's ESM loader,
// which — unlike Node — does not auto-append extensions. See browserext/README.md.
import { createRealChromeRuntime } from './chrome.js';
import type { ChromeRuntime } from './chrome.js';
import { createFetchApi } from './api.js';
import type { ApiClient } from './api.js';
import { createChromeStorage } from './storage.js';
import type { Storage } from './storage.js';
import { createNavMonitor } from './navMonitor.js';
import type { NavMonitor } from './navMonitor.js';
import { createWatchdog } from './watchdog.js';
import type { Watchdog } from './watchdog.js';

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
