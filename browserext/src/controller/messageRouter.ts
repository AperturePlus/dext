/** SW-side chrome.runtime.onMessage router (amend §4.1, §4.7, §7). This is the
 *  composition seam that finally connects the browser CS messages to the
 *  CrawlController built in slices 1–4 + the slice-5 command methods. It:
 *    - validates sender (own extension, top frame, allowed host) per amend §4.7;
 *    - routes REGISTER/TICK → receipt + sender-computed PanelState (TICK from the
 *      bound tab also triggers controller.tick(); non-bound TICK is read-only);
 *    - routes PAGE_READY/CAPTURE_RESULT/ACTION_PREPARED/ACTION_RESULT → deliver*;
 *    - routes COMMAND → the command method, enforcing the unbound-tab-only-bind
 *      rule and the non-preemptible binding (amend §7);
 *    - after a state-mutating command, calls broadcastPanelState to push
 *      STATE_CHANGED to the bound tab + the commanding sender (amend §7).
 *  Dependency-injected so node tests drive it with a fake controller + chrome.
 *
 *  The router itself only calls `chrome.onMessage(handle)` in `start()` to
 *  register the listener — it never sends messages (the controller does, via
 *  `sendMessage`). `background.ts` (Task 8) wires the real
 *  `chrome.runtime.onMessage.addListener` by passing a chrome object whose
 *  `onMessage` adapts it; `sendMessage` is present because the same chrome
 *  object is shared with the controller. */

import type { ChromeRuntime } from '../chrome.js';
import type { ApiClient } from '../api.js';
import type { CrawlController } from './controller.js';
import type { CsToSw, MessageReceipt, PanelCommand } from '../shared/rpc.js';
import type { ControllerState } from '../shared/state.js';
import { buildPanelState } from './panelState.js';
import { isAllowedFetchHost } from '../shared/hostPolicy.js';

export interface MessageSender {
  id?: string;
  tab?: { id: number; url?: string };
  frameId?: number;
  documentId?: string;
}

/** Chrome surface the router consumes: `sendMessage` (shared with the controller)
 *  + `onMessage` (registers the SW-side listener). `ChromeRuntime` itself does
 *  not expose `onMessage` as a method — `background.ts` adapts
 *  `chrome.runtime.onMessage.addListener` into this shape. The listener returns
 *  `true` (keep the channel open for an async reply), a `MessageReceipt` (the
 *  async reply itself, passed through for the test harness), `false` (no reply),
 *  or `undefined` (also no reply — matches chrome.runtime's "return nothing"
 *  convention). */
export interface MessageRouterChrome extends Pick<ChromeRuntime, 'sendMessage'> {
  onMessage(cb: (message: CsToSw, sender: MessageSender) => void | Promise<MessageReceipt | true | false | undefined>): void;
}

export interface MessageRouterDeps {
  controller: CrawlController;
  chrome: MessageRouterChrome;
  api: ApiClient;
  extensionId: string;
  now?: () => number;
  presentAction?: (tabId: number, url: string | undefined, state: ControllerState) => Promise<unknown>;
}

export interface MessageRouter {
  start(): void;
  handle(message: CsToSw, sender: MessageSender): Promise<MessageReceipt | true | false>;
}

function hostOf(url?: string): string {
  try { return url ? new URL(url).hostname : ''; } catch { return ''; }
}

export function createMessageRouter(deps: MessageRouterDeps): MessageRouter {
  const now = deps.now ?? (() => Date.now());

  async function routeCommand(cmd: PanelCommand, tabId: number, documentId?: string): Promise<void> {
    const c = deps.controller;
    switch (cmd.kind) {
      case 'bind': await c.start(tabId, now()); break;
      case 'unbind': await c.unbind(now()); break;
      case 'set_auto': await c.setAutoMode(cmd.value, now()); break;
      case 'set_paused': await c.setPaused(cmd.value, now()); break;
      case 'retry_capture': await c.retryCapture(documentId ?? '', now()); break;
      case 'open': await c.reopenCurrentJob(now()); break;
      case 'submit': await c.retryCapture(documentId ?? '', now()); break;
      case 'skip': await c.manualSkip(cmd.reason, now()); break;
      case 'fail': await c.manualFail(cmd.message, now()); break;
      case 'override': await c.overrideUrl(cmd.url, now()); break;
      case 'decision': await c.resolveDecision(cmd.id, cmd.action, now()); break;
    }
  }

  async function handle(message: CsToSw, sender: MessageSender): Promise<MessageReceipt | true | false> {
    // amend §4.7: own extension + top frame + allowed host only.
    if (sender.id !== deps.extensionId) return false;
    if (sender.frameId !== 0) return false;
    const tabId = sender.tab?.id;
    if (tabId === undefined) return false;
    if (!isAllowedFetchHost(hostOf(sender.tab?.url))) return false;
    const senderTabId: number = tabId;

    let state = await deps.controller.getState();

    async function projectState(): Promise<ReturnType<typeof buildPanelState>> {
      await deps.presentAction?.(senderTabId, sender.tab?.url, state);
      return buildPanelState(state, { tabId: senderTabId });
    }

    switch (message.op) {
      case 'REGISTER': {
        const ps = await projectState();
        return { received: true, state: ps };
      }
      case 'TICK': {
        // amend §7: bound-tab TICK reconciles; non-bound TICK is read-only.
        if (tabId === state.boundTabId) await deps.controller.tick(now());
        state = await deps.controller.getState();
        const ps = await projectState();
        return { received: true, state: ps };
      }
      case 'PAGE_READY': {
        await deps.controller.deliverPageReady({
          documentId: sender.documentId ?? '', url: message.url,
          detection: message.detection, timeStamp: now(),
        });
        return { received: true };
      }
      case 'CAPTURE_RESULT': {
        await deps.controller.deliverCaptureResult(message, sender);
        return { received: true };
      }
      case 'ACTION_PREPARED': {
        await deps.controller.deliverActionPrepared(message, sender);
        return { received: true };
      }
      case 'ACTION_RESULT': {
        await deps.controller.deliverActionResult(message, sender);
        return { received: true };
      }
      case 'COMMAND': {
        const bound = state.boundTabId;
        // amend §4.7: unbound tab may ONLY bind; a non-bound tab cannot issue other commands while bound.
        if (bound === null && message.command.kind !== 'bind') return { received: true };
        if (bound !== null && tabId !== bound && message.command.kind !== 'bind') return { received: true };
        await routeCommand(message.command, tabId, sender.documentId);
        state = await deps.controller.getState();
        await projectState();
        await deps.controller.broadcastPanelState({ tabId });
        return { received: true };
      }
      default: return false;
    }
  }

  return {
    start() {
      // Register the SW-side onMessage listener. The real chrome binding
      // (chrome.runtime.onMessage.addListener) is adapted into `chrome.onMessage`
      // by background.ts (Task 8); tests inject a fake that captures the
      // listener and lets `fire()` invoke it. The wrapper converts `false`
      // (validation failure / no reply) to `undefined` so a dropped message
      // yields no async-reply signal; a `MessageReceipt` (or `true`) is passed
      // through so the test harness (and the real listener, which treats any
      // truthy return as "keep channel open") sees the receipt.
      deps.chrome.onMessage(async (message, sender) => {
        const r = await handle(message, sender);
        return r === false ? undefined : r;
      });
    },
    handle,
  };
}
