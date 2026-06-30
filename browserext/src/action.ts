/** Toolbar action entry + per-tab status presentation.
 *
 * The action is an explicit user gesture: it may start the current supported
 * tab, but it never steals a binding from another tab. All Chrome calls are
 * injected so the behavior is covered without a live browser.
 */

import type { CrawlController } from './controller/controller.js';
import type { ControllerState } from './shared/state.js';
import { isAllowedFetchHost } from './shared/hostPolicy.js';

export interface ActionTab {
  id?: number;
  url?: string;
}

export interface ActionChrome {
  onClicked(cb: (tab: ActionTab) => void): void;
  setBadgeText(details: { tabId: number; text: string }): Promise<void>;
  setBadgeBackgroundColor(details: { tabId: number; color: string }): Promise<void>;
  setTitle(details: { tabId: number; title: string }): Promise<void>;
}

export type ActionStatus = 'ready' | 'on' | 'err' | 'busy' | 'unsupported';

export interface ToolbarAction {
  start(): void;
  present(tabId: number, url: string | undefined, state: ControllerState): Promise<ActionStatus>;
  handleClick(tab: ActionTab): Promise<ActionStatus | null>;
}

const STATUS_VIEW: Record<ActionStatus, { text: string; color: string; title: string }> = {
  ready: { text: '', color: '#2563EB', title: 'dext：点击绑定并开始' },
  on: { text: 'ON', color: '#16A34A', title: 'dext：已连接' },
  err: { text: 'ERR', color: '#DC2626', title: 'dext：后端不可达，将自动重试' },
  busy: { text: 'BUSY', color: '#D97706', title: 'dext：已绑定其他标签' },
  unsupported: { text: 'N/A', color: '#6B7280', title: 'dext：仅支持 *.edu.cn 和 *.github.io' },
};

function allowedUrl(url?: string): boolean {
  try {
    return !!url && isAllowedFetchHost(new URL(url).hostname);
  } catch {
    return false;
  }
}

export function actionStatusFor(
  state: ControllerState,
  tabId: number,
  url?: string,
): ActionStatus {
  if (!allowedUrl(url)) return 'unsupported';
  if (state.boundTabId !== null && state.boundTabId !== tabId) return 'busy';
  if (state.boundTabId === tabId) return state.connected ? 'on' : 'err';
  return 'ready';
}

export function createToolbarAction(deps: {
  controller: Pick<CrawlController, 'start' | 'getState' | 'broadcastPanelState'>;
  chrome: ActionChrome;
  now?: () => number;
}): ToolbarAction {
  const now = deps.now ?? (() => Date.now());

  async function present(
    tabId: number,
    url: string | undefined,
    state: ControllerState,
  ): Promise<ActionStatus> {
    const status = actionStatusFor(state, tabId, url);
    const view = STATUS_VIEW[status];
    await Promise.all([
      deps.chrome.setBadgeText({ tabId, text: view.text }),
      deps.chrome.setBadgeBackgroundColor({ tabId, color: view.color }),
      deps.chrome.setTitle({
        tabId,
        title: status === 'on' ? `${view.title} · ${state.phase}` : view.title,
      }),
    ]);
    return status;
  }

  async function handleClick(tab: ActionTab): Promise<ActionStatus | null> {
    if (tab.id === undefined) return null;
    const before = await deps.controller.getState();
    if (!allowedUrl(tab.url)) return present(tab.id, tab.url, before);
    if (before.boundTabId !== null && before.boundTabId !== tab.id) {
      return present(tab.id, tab.url, before);
    }

    await deps.controller.start(tab.id, now());
    const after = await deps.controller.getState();
    await deps.controller.broadcastPanelState({ tabId: tab.id });
    return present(tab.id, tab.url, after);
  }

  return {
    start() {
      deps.chrome.onClicked((tab) => { void handleClick(tab); });
    },
    present,
    handleClick,
  };
}

export function createRealActionChrome(): ActionChrome {
  return {
    onClicked(cb) { chrome.action.onClicked.addListener(cb); },
    async setBadgeText(details) { await chrome.action.setBadgeText(details); },
    async setBadgeBackgroundColor(details) { await chrome.action.setBadgeBackgroundColor(details); },
    async setTitle(details) { await chrome.action.setTitle(details); },
  };
}
