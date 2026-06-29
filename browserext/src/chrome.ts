/** Injectable adapter over the MV3 chrome.* surface. Consumers (navMonitor,
 * controller) depend on the ChromeRuntime interface so tests inject fakes.
 * createRealChromeRuntime binds the real chrome.* APIs and is used only by
 * background.ts (manual verification). */

import type {
  BeforeRequestEvent,
  BeforeRedirectEvent,
  CommittedEvent,
} from './shared/navEvents.js';

export interface NavCompletedEvent {
  tabId: number;
  url: string;
  statusCode: number;
  frameId: number;
  requestId: string;
  documentId?: string;
  timeStamp: number;
}

export interface NavErrorEvent {
  tabId: number;
  url: string;
  error: string;
  frameId: number;
  requestId: string;
  timeStamp: number;
}

export interface ChromeRuntime {
  onNavCompleted(cb: (e: NavCompletedEvent) => void): void;
  onNavError(cb: (e: NavErrorEvent) => void): void;
  onBeforeRequest(cb: (e: BeforeRequestEvent) => void): void;
  onBeforeRedirect(cb: (e: BeforeRedirectEvent) => void): void;
  onCommitted(cb: (e: CommittedEvent) => void): void;
  onHistoryStateUpdated(cb: (e: CommittedEvent) => void): void;
  updateTabUrl(tabId: number, url: string): Promise<void>;
  findOwnerTab(): Promise<number | null>;
  getTab(tabId: number): Promise<{ id: number; url?: string } | null>;
  sendMessage(tabId: number, message: unknown, options?: { documentId?: string; frameId?: number }): Promise<unknown>;
  registerAlarm(name: string, periodMinutes: number, cb: () => void): void;
}

const ALLOWED_HOST_SUFFIXES = ['edu.cn', 'github.io'];

const registeredAlarms = new Set<string>();

function isAllowedHost(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return ALLOWED_HOST_SUFFIXES.some((s) => h === s || h.endsWith(`.${s}`));
}

export function createRealChromeRuntime(): ChromeRuntime {
  return {
    onNavCompleted(cb) {
      chrome.webRequest.onCompleted.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({
            tabId: details.tabId, url: details.url, statusCode: details.statusCode,
            frameId: details.frameId, requestId: details.requestId,
            documentId: (details as chrome.webRequest.WebResponseDetails & { documentId?: string }).documentId,
            timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onNavError(cb) {
      chrome.webRequest.onErrorOccurred.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({
            tabId: details.tabId, url: details.url, error: details.error,
            frameId: details.frameId, requestId: details.requestId, timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onBeforeRequest(cb) {
      chrome.webRequest.onBeforeRequest.addListener(
        (details) => {
          if (details.type !== 'main_frame') return;
          cb({
            tabId: details.tabId, frameId: details.frameId, type: details.type,
            url: details.url, requestId: details.requestId, timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onBeforeRedirect(cb) {
      chrome.webRequest.onBeforeRedirect.addListener(
        (details) => {
          if (details.type !== 'main_frame') return;
          cb({
            tabId: details.tabId, frameId: details.frameId, type: details.type,
            url: details.url, redirectUrl: details.redirectUrl, requestId: details.requestId,
            timeStamp: details.timeStamp,
          });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onCommitted(cb) {
      chrome.webNavigation.onCommitted.addListener((details) => {
        if (details.frameId !== 0) return;
        cb({
          tabId: details.tabId, frameId: details.frameId, documentId: details.documentId ?? '',
          url: details.url, timeStamp: details.timeStamp,
        });
      });
    },
    onHistoryStateUpdated(cb) {
      chrome.webNavigation.onHistoryStateUpdated.addListener((details) => {
        if (details.frameId !== 0) return;
        cb({
          tabId: details.tabId, frameId: details.frameId, documentId: details.documentId ?? '',
          url: details.url, timeStamp: details.timeStamp,
        });
      });
    },
    async updateTabUrl(tabId, url) {
      await chrome.tabs.update(tabId, { url });
    },
    async findOwnerTab() {
      const tabs = await chrome.tabs.query({});
      // Prefer a tab on an allowed fetch host; fall back to the active tab.
      for (const t of tabs) {
        try {
          if (t.url && isAllowedHost(new URL(t.url).hostname)) return t.id ?? null;
        } catch {
          // ignore non-URL tab URLs (about:blank, chrome-error, etc.)
        }
      }
      const active = await chrome.tabs.query({ active: true, currentWindow: true });
      return active[0]?.id ?? null;
    },
    async getTab(tabId) {
      try {
        const t = await chrome.tabs.get(tabId);
        if (!t) return null;
        return { id: t.id ?? tabId, url: t.url };
      } catch {
        return null;   // tab gone (e.g. TypeError "No tab with id") → invalid bound tab
      }
    },
    async sendMessage(tabId, message, options) {
      return await chrome.tabs.sendMessage(tabId, message, options ?? {});
    },
    registerAlarm(name, periodMinutes, cb) {
      if (registeredAlarms.has(name)) return;
      registeredAlarms.add(name);
      chrome.alarms.create(name, { periodInMinutes: periodMinutes });
      chrome.alarms.onAlarm.addListener((alarm) => {
        if (alarm.name === name) cb();
      });
    },
  };
}
