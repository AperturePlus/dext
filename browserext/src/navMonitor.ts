/** Thin main-frame navigation-event adapter (amend §3.2). It ONLY:
 *    - registers the six webRequest/webNavigation listeners;
 *    - scope-filters main-frame + bound tab (lock-free via controller.getNavScope());
 *    - forwards the RAW event (+ outcome for the final onCompleted/onErrorOccurred) to the Controller.
 *  It does NOT hold api/storage, does NOT call fail/skip, does NOT accumulate gateway
 *  counts, does NOT call tabs.update. onBeforeRedirect is forwarded RAW — it never calls
 *  the classifier (amend §3.1); the Controller runs the same-crawl-site gate on redirectUrl.
 *  The Controller (NavController) owns all requestId/documentId/job/time/phase correlation
 *  under its mutex (amend §2.1–§2.2) and the retry funnel (amend §3.2). */

import type { ChromeRuntime } from './chrome.js';
import { classifyNavigation } from './status.js';
import { isMainFrame } from './shared/navEvents.js';
import type { NavController } from './shared/navEvents.js';

export interface NavMonitorDeps {
  chrome: Pick<
    ChromeRuntime,
    'onBeforeRequest' | 'onBeforeRedirect' | 'onCommitted' | 'onHistoryStateUpdated' | 'onNavCompleted' | 'onNavError'
  >;
  controller: NavController;
}

export interface NavMonitor {
  start(): void;
}

export function createNavMonitor(deps: NavMonitorDeps): NavMonitor {
  const { chrome, controller } = deps;

  function boundTabMatches(tabId: number): boolean {
    return controller.getNavScope().boundTabId === tabId;
  }

  return {
    start() {
      chrome.onBeforeRequest((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverBeforeRequest(e);
      });
      chrome.onBeforeRedirect((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverBeforeRedirect(e);    // RAW — no classifier (amend §3.1)
      });
      chrome.onCommitted((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverCommitted(e);
      });
      chrome.onHistoryStateUpdated((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        void controller.deliverCommitted(e);          // SPA same-document → treat as commit (slice 4 refines)
      });
      chrome.onNavCompleted((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        const outcome = classifyNavigation({ kind: 'completed', statusCode: e.statusCode });
        void controller.deliverHttpEvent(e, outcome);
      });
      chrome.onNavError((e) => {
        if (!isMainFrame(e) || !boundTabMatches(e.tabId)) return;
        const outcome = classifyNavigation({ kind: 'error', error: e.error });
        void controller.deliverError(e, outcome);
      });
    },
  };
}
