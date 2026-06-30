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
import { createMessageRouter } from './controller/messageRouter.js';
import type { MessageRouterChrome } from './controller/messageRouter.js';
import { createRealActionChrome, createToolbarAction } from './action.js';
import type { ToolbarAction } from './action.js';
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
  toolbarAction: ToolbarAction | null;
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

  // Slice 5: wire the SW-side chrome.runtime.onMessage router. The router's
  // `start()` calls `deps.chrome.onMessage(cb)`, but `ChromeRuntime` has no
  // `onMessage` method — it ships `sendMessage` (shared with the controller).
  // Build a MessageRouterChrome adapter here: `sendMessage` delegates to the
  // injected chrome, `onMessage` adapts the real chrome.runtime.onMessage's
  // 3-arg (msg, sender, sendResponse) chrome signature into the router's
  // 2-arg cb(msg, sender) => reply, wiring the async reply into sendResponse
  // and returning `true` (keep the channel open for the async reply). The
  // `false → undefined` conversion lives in start() (one place, not here).
  // Guarded so the existing background.test.mjs fake (no chrome.runtime)
  // no-ops harmlessly.
  const routerChrome: MessageRouterChrome = {
    sendMessage: (tabId, message, options) => deps.chrome.sendMessage(tabId, message, options),
    onMessage: (cb) => {
      if (typeof chrome === 'undefined' || !chrome.runtime?.onMessage) return;
      chrome.runtime.onMessage.addListener(
        (msg: unknown, sender: unknown, sendResponse: (r: unknown) => void) => {
          void Promise.resolve(cb(msg as never, sender as never))
            .then((reply) => { try { sendResponse(reply); } catch { /* channel closed */ } });
          return true;   // keep the channel open for the async sendResponse
        },
      );
    },
  };
  const toolbarAction = (typeof chrome !== 'undefined' && chrome.action?.onClicked)
    ? createToolbarAction({ controller, chrome: createRealActionChrome() })
    : null;
  const router = createMessageRouter({
    controller,
    chrome: routerChrome,
    api: deps.api,
    extensionId: (typeof chrome !== 'undefined' && chrome.runtime?.id) ? chrome.runtime.id : 'dext',
    presentAction: toolbarAction
      ? (tabId, url, state) => toolbarAction.present(tabId, url, state)
      : undefined,
  });
  router.start();
  toolbarAction?.start();
  return { navMonitor, controller, toolbarAction };
}

// Self-invoke on SW startup with real implementations, but skip in node tests where
// chrome is undefined. The typeof check keeps both tsc and the test harness happy.
if (typeof chrome !== 'undefined' && chrome.storage?.local) {
  wireBackground({
    chrome: createRealChromeRuntime(),
    api: createFetchApi(API_BASE),
  });
}
