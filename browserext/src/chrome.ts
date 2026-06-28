/** Injectable adapter over the MV3 chrome.* surface. Consumers (navMonitor,
 * controller) depend on the ChromeRuntime interface so tests inject fakes.
 * createRealChromeRuntime binds the real chrome.* APIs and is used only by
 * background.ts (manual verification). */

export interface NavCompletedEvent {
  tabId: number;
  url: string;
  statusCode: number;
  frameId: number;
}

export interface NavErrorEvent {
  tabId: number;
  url: string;
  error: string;
  frameId: number;
}

export interface ChromeRuntime {
  onNavCompleted(cb: (e: NavCompletedEvent) => void): void;
  onNavError(cb: (e: NavErrorEvent) => void): void;
  updateTabUrl(tabId: number, url: string): Promise<void>;
  findOwnerTab(): Promise<number | null>;
  getTab(tabId: number): Promise<{ id: number; url?: string } | null>;
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
      // Main frame only (frameId === 0) — sub-frame errors are noise.
      chrome.webRequest.onCompleted.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({ tabId: details.tabId, url: details.url, statusCode: details.statusCode, frameId: details.frameId });
        },
        { urls: ['<all_urls>'] },
      );
    },
    onNavError(cb) {
      chrome.webRequest.onErrorOccurred.addListener(
        (details) => {
          if (details.frameId !== 0) return;
          cb({ tabId: details.tabId, url: details.url, error: details.error, frameId: details.frameId });
        },
        { urls: ['<all_urls>'] },
      );
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
