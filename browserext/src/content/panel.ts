/** Content-script control panel (spec §4.9, amend §7). Mounted in a Shadow DOM
 *  so its CSS never collides with the host page. Renders the existing userscript
 *  controls (auto/pause/open/submit/skip/fail/override-url) PLUS bind/unbind and
 *  the inlined pending-decision (NO window.confirm — amend §4.9). DOM is injected
 *  as a PanelDom surface so node tests drive it without a real document.
 *
 *  Click handling is split: mountPanel wires ONE click listener on the shadow root
 *  that reads the clicked element's data-cmd/data-* and calls commandFromClick,
 *  then forwards the PanelCommand to the onCommand callback. commandFromClick is
 *  pure and unit-tested directly. */

import type { PanelCommand, PanelState } from '../shared/rpc.js';
import { formatError } from './panelState.js';

export interface PanelShadowRoot {
  innerHTML: string;
  querySelector(sel: string): { dataset: Record<string, string> } | null;
  querySelectorAll(sel: string): Array<{ dataset: Record<string, string> }>;
  addEventListener(type: 'click', cb: (e: { target: unknown }) => void): void;
}

export interface PanelDom {
  host: {
    attachShadow(init: { mode: 'open' }): PanelShadowRoot;
    /** Implementation-detail counter incremented on each attachShadow call so
     *  tests can assert idempotency. Not read by production code. */
    attachShadowCalls?: number;
    /** Internal cache slot — production code stores the live shadow root here so
     *  a second mountPanel on the same host reuses it instead of re-attaching. */
    __dextShadow?: PanelShadowRoot;
  };
  document?: unknown;
}

/** Inlined CSS — a TS string so no esbuild loader change is needed and the
 *  panel stays unit-testable. Spec §5.4 mentioned the .css text-loader for
 *  imported CSS; the panel deliberately keeps its CSS inline. */
export const PANEL_CSS = `
#dext-panel { font: 13px/1.4 system-ui, sans-serif; padding: 8px; }
.dext-row { margin: 4px 0; }
.dext-btn { margin-right: 4px; cursor: pointer; }
.dext-err { color: #b00; }
.dext-muted { color: #666; }
`;

export interface MountedPanel {
  render(state: PanelState): void;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/"/g, '&quot;');
}

/** Pure: map a clicked element's dataset + form values to a PanelCommand, or null. */
export function commandFromClick(
  target: { dataset: Record<string, string> } | null,
  formData: (id: string) => string | null,
): PanelCommand | null {
  if (!target) return null;
  const d = target.dataset;
  switch (d.cmd) {
    case 'bind': return { kind: 'bind' };
    case 'unbind': return { kind: 'unbind' };
    case 'open': return { kind: 'open' };
    case 'submit': return { kind: 'submit' };
    case 'skip': return { kind: 'skip', reason: d.reason };
    case 'fail': return { kind: 'fail', message: undefined };
    case 'set_auto': return { kind: 'set_auto', value: d.value === 'true' };
    case 'set_paused': return { kind: 'set_paused', value: d.value === 'true' };
    case 'override': {
      const url = formData('dext-override-url');
      if (!url) return null;
      return { kind: 'override', url };
    }
    case 'decision': {
      if (!d.id || !d.action) return null;
      return { kind: 'decision', id: d.id, action: d.action };
    }
    default: return null;
  }
}

export function mountPanel(
  dom: PanelDom,
  onCommand?: (cmd: PanelCommand) => void,
  /** Optional injected reader for live input values (e.g. the override-URL
   *  input). Task 7: when omitted, the click listener falls back to `() => null`
   *  (back-compat with Task-3 callers). When provided (the content script wires
   *  one that reads `shadowRoot.getElementById('dext-override-url')?.value`),
   *  the override command reads the live input value instead of being dead. */
  formData?: (id: string) => string | null,
): MountedPanel {
  let shadow: PanelShadowRoot | null = null;
  const readForm = formData ?? (() => null);

  function ensureShadow(): PanelShadowRoot {
    if (shadow) return shadow;
    // Cache on the host so a second mountPanel on the same host reuses the
    // existing shadow root rather than calling attachShadow again. This is the
    // real idempotency property — attachShadow is invoked at most once per host.
    const host = dom.host as PanelDom['host'];
    if (host.__dextShadow) {
      shadow = host.__dextShadow;
      return shadow;
    }
    host.attachShadowCalls = (host.attachShadowCalls ?? 0) + 1;
    shadow = host.attachShadow({ mode: 'open' });
    host.__dextShadow = shadow;
    if (onCommand) {
      shadow.addEventListener('click', (e) => {
        const t = e.target as { dataset?: Record<string, string> } | null;
        const cmd = commandFromClick((t && t.dataset) ? { dataset: t.dataset } : null, readForm);
        if (cmd) onCommand(cmd);
      });
    }
    return shadow;
  }

  function render(state: PanelState): void {
    const sh = ensureShadow();
    const errText = formatError(state.lastError);
    const parts: string[] = [];
    parts.push(`<style>${PANEL_CSS}</style>`);
    parts.push(`<div id="dext-panel">`);
    if (!state.bound) {
      parts.push(`<div class="dext-row"><button class="dext-btn" data-cmd="bind">绑定并开始</button></div>`);
    } else if (!state.isBoundTab) {
      parts.push(`<div class="dext-row dext-muted">已绑定其他标签</div>`);
    } else {
      parts.push(`<div class="dext-row">phase: ${escapeHtml(state.phase)} · attempt ${state.navigationAttempt}</div>`);
      parts.push(`<div class="dext-row">connected: ${state.connected ? 'ok' : 'down'}</div>`);
      parts.push(`<div class="dext-row">auto: <button class="dext-btn" data-cmd="set_auto" data-value="${state.autoMode ? 'false' : 'true'}">${state.autoMode ? '关闭' : '开启'}</button> · paused: <button class="dext-btn" data-cmd="set_paused" data-value="${state.paused ? 'false' : 'true'}">${state.paused ? '继续' : '暂停'}</button></div>`);
      parts.push(`<div class="dext-row">`);
      parts.push(`<button class="dext-btn" data-cmd="open">打开</button>`);
      parts.push(`<button class="dext-btn" data-cmd="submit">提交</button>`);
      parts.push(`<button class="dext-btn" data-cmd="skip">跳过</button>`);
      parts.push(`<button class="dext-btn" data-cmd="fail">失败</button>`);
      parts.push(`</div>`);
      parts.push(`<div class="dext-row"><input id="dext-override-url" placeholder="override url"/><button class="dext-btn" data-cmd="override">覆盖</button></div>`);
      parts.push(`<div class="dext-row"><button class="dext-btn" data-cmd="unbind">解除绑定</button></div>`);
      if (errText) parts.push(`<div class="dext-row dext-err">${escapeHtml(errText)}</div>`);
      const dec = state.pendingDecision;
      if (dec) {
        parts.push(`<div class="dext-row" id="dext-decision">`);
        parts.push(`<div>待处理决策: ${escapeHtml(dec.id)} (${escapeHtml(dec.suggested_action)})</div>`);
        parts.push(`<button class="dext-btn" data-cmd="decision" data-id="${escapeHtml(dec.id)}" data-action="accept">接受</button>`);
        parts.push(`<button class="dext-btn" data-cmd="decision" data-id="${escapeHtml(dec.id)}" data-action="skip">拒绝</button>`);
        parts.push(`</div>`);
      }
    }
    parts.push(`</div>`);
    sh.innerHTML = parts.join('');
  }

  return { render };
}
