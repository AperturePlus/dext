/** Accessible Shadow-DOM control card for the bound crawler tab. */

import type { PanelCommand, PanelState } from '../shared/rpc.js';
import { describeError, panelProgress } from './panelState.js';

export interface PanelShadowRoot {
  innerHTML: string;
  querySelector(sel: string): { dataset: Record<string, string> } | null;
  querySelectorAll(sel: string): Array<{ dataset: Record<string, string> }>;
  addEventListener(type: 'click', cb: (e: { target: unknown }) => void): void;
}

export interface PanelDom {
  host: {
    attachShadow(init: { mode: 'open' }): PanelShadowRoot;
    attachShadowCalls?: number;
    __dextShadow?: PanelShadowRoot;
  };
  document?: unknown;
}

export interface PanelUiState {
  collapsed: boolean;
  moreOpen: boolean;
}

export const PANEL_CSS = `
:host {
  --dext-bg: #11161c;
  --dext-raised: #181e25;
  --dext-hover: #202832;
  --dext-border: #343c47;
  --dext-border-soft: #28303a;
  --dext-text: #f4f7fb;
  --dext-muted: #99a3af;
  --dext-dim: #6f7986;
  --dext-blue: #347df1;
  --dext-blue-hover: #438bfa;
  --dext-green: #71d276;
  --dext-orange: #f5ad53;
  --dext-red: #ff7178;
  position: fixed;
  top: 14px;
  right: 14px;
  z-index: 2147483647;
  display: block;
  width: min(360px, calc(100vw - 28px));
  color: var(--dext-text);
  color-scheme: dark;
}
* { box-sizing: border-box; }
button, input { font: inherit; }
button { color: inherit; }
button:focus-visible, input:focus-visible {
  outline: 2px solid #7bacff;
  outline-offset: 2px;
}
.dext-card {
  overflow: hidden;
  width: 100%;
  border: 1px solid var(--dext-border);
  border-radius: 14px;
  background: rgba(17, 22, 28, .98);
  box-shadow: 0 18px 48px rgba(0, 0, 0, .44), 0 2px 8px rgba(0, 0, 0, .32);
  font: 13px/1.45 Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  letter-spacing: 0;
}
.dext-card.is-collapsed .dext-header { border-bottom: 0; }
.dext-header {
  display: flex;
  align-items: center;
  min-height: 58px;
  padding: 0 14px;
  border-bottom: 1px solid var(--dext-border-soft);
}
.dext-brand {
  display: flex;
  min-width: 0;
  align-items: center;
  gap: 10px;
  font-size: 15px;
  font-weight: 680;
  letter-spacing: -.01em;
}
.dext-logo {
  display: grid;
  width: 29px;
  height: 29px;
  flex: 0 0 29px;
  place-items: center;
  border-radius: 8px;
  background: var(--dext-blue);
  box-shadow: inset 0 0 0 1px rgba(255,255,255,.14);
  color: #fff;
  font-size: 18px;
  font-weight: 760;
  line-height: 1;
}
.dext-status {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  margin-left: auto;
  padding: 4px 8px;
  border-radius: 999px;
  background: rgba(113, 210, 118, .1);
  color: #9de5a1;
  font-size: 11px;
  font-weight: 650;
  white-space: nowrap;
}
.dext-status::before { content: ""; width: 6px; height: 6px; border-radius: 50%; background: currentColor; box-shadow: 0 0 0 3px rgba(113,210,118,.11); }
.dext-status.is-offline { background: rgba(255,113,120,.1); color: #ff9ca1; }
.dext-status.is-busy { background: rgba(245,173,83,.1); color: #ffc16f; }
.dext-status.is-idle { background: rgba(153,163,175,.1); color: #b5bec8; }
.dext-icon-btn {
  display: grid;
  width: 32px;
  height: 32px;
  margin-left: 7px;
  padding: 0;
  place-items: center;
  border: 0;
  border-radius: 8px;
  background: transparent;
  cursor: pointer;
}
.dext-icon-btn:hover { background: var(--dext-hover); }
.dext-icon-btn svg { width: 17px; height: 17px; transition: transform .16s ease; }
.is-collapsed .dext-icon-btn svg { transform: rotate(180deg); }
.dext-body { padding: 15px; }
.dext-progress {
  display: grid;
  grid-template-columns: 1fr 30px 1fr 30px 1fr;
  align-items: start;
  margin: 0 1px 17px;
}
.dext-step { display: grid; justify-items: center; gap: 6px; color: var(--dext-dim); font-size: 11px; font-weight: 620; }
.dext-step-dot {
  display: grid;
  width: 23px;
  height: 23px;
  place-items: center;
  border: 1px solid var(--dext-border);
  border-radius: 50%;
  background: var(--dext-bg);
  color: var(--dext-muted);
  font-size: 11px;
}
.dext-step[data-state="active"] { color: #a8c9ff; }
.dext-step[data-state="active"] .dext-step-dot { border-color: var(--dext-blue); background: var(--dext-blue); color: white; box-shadow: 0 0 0 4px rgba(52,125,241,.13); }
.dext-step[data-state="complete"] { color: #aab3bd; }
.dext-step[data-state="complete"] .dext-step-dot { border-color: rgba(113,210,118,.56); background: rgba(113,210,118,.12); color: var(--dext-green); }
.dext-step[data-state="error"] { color: #ff9ca1; }
.dext-step[data-state="error"] .dext-step-dot { border-color: rgba(255,113,120,.62); background: rgba(255,113,120,.12); color: var(--dext-red); }
.dext-connector { height: 1px; margin-top: 11px; background: var(--dext-border); }
.dext-connector.is-complete { background: rgba(113,210,118,.55); }
.dext-job {
  padding: 12px;
  border: 1px solid var(--dext-border-soft);
  border-radius: 10px;
  background: var(--dext-raised);
}
.dext-job-top { display: flex; align-items: center; gap: 8px; }
.dext-school { min-width: 0; overflow: hidden; color: var(--dext-text); font-size: 14px; font-weight: 660; text-overflow: ellipsis; white-space: nowrap; }
.dext-attempt { margin-left: auto; color: var(--dext-muted); font-size: 11px; white-space: nowrap; }
.dext-target { display: block; overflow: hidden; margin-top: 4px; color: #86919e; font-size: 11px; text-overflow: ellipsis; white-space: nowrap; }
.dext-controls { display: grid; grid-template-columns: 1fr auto; align-items: center; gap: 10px; margin-top: 13px; }
.dext-auto { display: flex; align-items: center; gap: 9px; color: #dbe1e8; font-weight: 590; }
.dext-switch { position: relative; width: 36px; height: 20px; padding: 0; border: 0; border-radius: 999px; background: #39434f; cursor: pointer; transition: background .16s ease; }
.dext-switch[aria-checked="true"] { background: var(--dext-blue); }
.dext-switch::after { content: ""; position: absolute; top: 3px; left: 3px; width: 14px; height: 14px; border-radius: 50%; background: white; box-shadow: 0 1px 3px rgba(0,0,0,.35); transition: transform .16s ease; }
.dext-switch[aria-checked="true"]::after { transform: translateX(16px); }
.dext-btn {
  display: inline-flex;
  min-height: 36px;
  align-items: center;
  justify-content: center;
  gap: 7px;
  padding: 0 12px;
  border: 1px solid var(--dext-border);
  border-radius: 8px;
  background: var(--dext-raised);
  color: #e8edf3;
  font-weight: 610;
  cursor: pointer;
}
.dext-btn:hover { border-color: #46515e; background: var(--dext-hover); }
.dext-btn:disabled { cursor: not-allowed; opacity: .45; }
.dext-btn svg { width: 15px; height: 15px; }
.dext-btn-primary { width: 100%; min-height: 39px; border-color: var(--dext-blue); background: var(--dext-blue); color: white; }
.dext-btn-primary:hover { border-color: var(--dext-blue-hover); background: var(--dext-blue-hover); }
.dext-error {
  display: grid;
  grid-template-columns: 21px 1fr;
  gap: 9px;
  margin-top: 13px;
  padding: 11px;
  border: 1px solid rgba(255,113,120,.34);
  border-radius: 9px;
  background: rgba(255,113,120,.075);
}
.dext-error-icon { display: grid; width: 20px; height: 20px; place-items: center; border-radius: 50%; background: rgba(255,113,120,.16); color: var(--dext-red); font-weight: 750; }
.dext-error-title { color: #ffd5d7; font-weight: 680; }
.dext-error-copy { margin-top: 2px; color: #b9a2a5; font-size: 11px; }
.dext-primary-wrap { margin-top: 11px; }
.dext-actions { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 9px; }
.dext-more-wrap { position: relative; margin-top: 8px; }
.dext-more { width: 100%; min-height: 31px; border: 0; background: transparent; color: var(--dext-muted); font-size: 12px; }
.dext-more-menu { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin-top: 6px; padding: 8px; border: 1px solid var(--dext-border-soft); border-radius: 9px; background: var(--dext-raised); }
.dext-more-menu .dext-btn { min-height: 32px; color: #c9d0d8; font-size: 12px; }
.dext-override { display: grid; grid-template-columns: minmax(0,1fr) auto; gap: 7px; margin-top: 12px; }
.dext-input { width: 100%; min-width: 0; height: 36px; padding: 0 10px; border: 1px solid var(--dext-border); border-radius: 8px; background: #0d1217; color: var(--dext-text); }
.dext-input::placeholder { color: #687380; }
.dext-footer { display: flex; justify-content: center; margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--dext-border-soft); }
.dext-link-btn { border: 0; background: transparent; color: #818c98; font-size: 11px; cursor: pointer; }
.dext-link-btn:hover { color: #c5cdd6; }
.dext-notice { padding: 15px; }
.dext-notice-card { padding: 13px; border: 1px solid var(--dext-border-soft); border-radius: 10px; background: var(--dext-raised); color: #b5bec8; }
.dext-notice-title { margin-bottom: 4px; color: var(--dext-text); font-weight: 680; }
.dext-notice-copy { font-size: 12px; }
.dext-notice .dext-btn-primary { margin-top: 12px; }
.dext-decision { margin-top: 12px; padding: 11px; border: 1px solid rgba(245,173,83,.35); border-radius: 9px; background: rgba(245,173,83,.07); }
.dext-decision-title { color: #ffd193; font-weight: 660; }
.dext-decision-actions { display: flex; gap: 8px; margin-top: 8px; }
@media (max-width: 420px) {
  :host { top: 8px; right: 8px; width: calc(100vw - 16px); }
  .dext-body { padding: 13px; }
  .dext-actions { grid-template-columns: 1fr; }
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { scroll-behavior: auto !important; transition: none !important; }
}
`;

export interface MountedPanel {
  render(state: PanelState): void;
}

function escapeHtml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function icon(name: 'chevron' | 'pause' | 'play' | 'reload' | 'capture'): string {
  const paths = {
    chevron: '<path d="m5 7 5 5 5-5"/>',
    pause: '<path d="M7 5v10M13 5v10"/>',
    play: '<path d="m7 5 8 5-8 5Z"/>',
    reload: '<path d="M15 7V4l-2 2a6 6 0 1 0 2.2 6"/>',
    capture: '<path d="M5 6V4h2M13 4h2v2M15 14v2h-2M7 16H5v-2M7 10h6"/>',
  } as const;
  return `<svg aria-hidden="true" viewBox="0 0 20 20" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round">${paths[name]}</svg>`;
}

function compactTarget(url: string): string {
  try {
    const parsed = new URL(url);
    return `${parsed.host}${parsed.pathname === '/' ? '' : parsed.pathname}${parsed.search}`;
  } catch {
    return url;
  }
}

function step(label: string, index: number, state: string): string {
  const mark = state === 'complete' ? '✓' : state === 'error' ? '!' : String(index);
  return `<div class="dext-step" data-state="${state}"><span class="dext-step-dot">${mark}</span><span>${label}</span></div>`;
}

function header(state: PanelState, ui: PanelUiState, status: { label: string; cls: string }): string {
  return `<div class="dext-header">
    <div class="dext-brand"><span class="dext-logo" aria-hidden="true">d</span><span>dext crawler</span></div>
    <span class="dext-status ${status.cls}" role="status">${status.label}</span>
    <button type="button" class="dext-icon-btn" data-ui="collapse" aria-label="${ui.collapsed ? '展开面板' : '折叠面板'}" aria-expanded="${!ui.collapsed}">${icon('chevron')}</button>
  </div>`;
}

export function renderPanelHtml(state: PanelState, ui: PanelUiState = { collapsed: false, moreOpen: false }): string {
  const status = !state.bound
    ? { label: '未绑定', cls: 'is-idle' }
    : !state.isBoundTab
      ? { label: '占用中', cls: 'is-busy' }
      : state.connected
        ? { label: '已连接', cls: '' }
        : { label: '后端离线', cls: 'is-offline' };
  const cardClass = ui.collapsed ? 'dext-card is-collapsed' : 'dext-card';
  const parts = [`<style>${PANEL_CSS}</style><section id="dext-panel" class="${cardClass}" aria-label="dext crawler 控制面板">`, header(state, ui, status)];
  if (ui.collapsed) {
    parts.push('</section>');
    return parts.join('');
  }

  if (!state.bound) {
    parts.push(`<div class="dext-notice"><div class="dext-notice-card"><div class="dext-notice-title">尚未绑定当前标签</div><div class="dext-notice-copy">后端不会主动打开网页。请在支持的学校页面绑定后开始任务。</div></div><button type="button" class="dext-btn dext-btn-primary" data-cmd="bind">绑定并开始</button></div>`);
    parts.push('</section>');
    return parts.join('');
  }

  if (!state.isBoundTab) {
    parts.push(`<div class="dext-notice"><div class="dext-notice-card"><div class="dext-notice-title">已绑定其他标签</div><div class="dext-notice-copy">单标签安全模式禁止抢占。请回到已绑定标签继续操作。</div></div></div>`);
    parts.push('</section>');
    return parts.join('');
  }

  const progress = panelProgress(state);
  const error = describeError(state.lastError);
  const captureFailure = state.lastError?.kind === 'content_unavailable' && state.lastError.missing === 'capture_result';
  const job = state.currentJob;
  const fullUrl = job?.url ?? '';
  const school = job?.context.university_name || '等待任务';
  const disabled = job ? '' : ' disabled';
  parts.push(`<div class="dext-body">
    <div class="dext-progress" aria-label="任务进度">
      ${step('导航', 1, progress.navigation)}<span class="dext-connector ${progress.navigation === 'complete' ? 'is-complete' : ''}"></span>
      ${step('捕获', 2, progress.capture)}<span class="dext-connector ${progress.capture === 'complete' ? 'is-complete' : ''}"></span>
      ${step('提交', 3, progress.submit)}
    </div>
    <div class="dext-job">
      <div class="dext-job-top"><span class="dext-school" title="${escapeHtml(school)}">${escapeHtml(school)}</span><span class="dext-attempt">尝试 ${Math.max(1, state.navigationAttempt)}</span></div>
      <span class="dext-target" title="${escapeHtml(fullUrl)}">${fullUrl ? escapeHtml(compactTarget(fullUrl)) : '等待后端分配任务'}</span>
    </div>
    <div class="dext-controls">
      <label class="dext-auto"><button type="button" class="dext-switch" role="switch" aria-label="自动运行" aria-checked="${state.autoMode}" data-cmd="set_auto" data-value="${!state.autoMode}"></button><span>自动运行</span></label>
      <button type="button" class="dext-btn" data-cmd="set_paused" data-value="${!state.paused}">${icon(state.paused ? 'play' : 'pause')}<span>${state.paused ? '继续' : '暂停'}</span></button>
    </div>`);

  if (error) {
    parts.push(`<div class="dext-error" role="alert"><span class="dext-error-icon">!</span><div><div class="dext-error-title">${escapeHtml(error.title)}</div><div class="dext-error-copy">${escapeHtml(error.description)}</div></div></div>`);
  }
  if (captureFailure) {
    parts.push(`<div class="dext-primary-wrap"><button type="button" class="dext-btn dext-btn-primary" data-cmd="retry_capture">${icon('capture')}<span>重试捕获</span></button></div>`);
  }

  parts.push(`<div class="dext-actions">
      <button type="button" class="dext-btn" data-cmd="open"${disabled}>${icon('reload')}<span>重新打开</span></button>
      <button type="button" class="dext-btn" data-cmd="submit"${disabled}>${icon('capture')}<span>提交当前页</span></button>
    </div>
    <div class="dext-more-wrap">
      <button type="button" class="dext-btn dext-more" data-ui="more" aria-expanded="${ui.moreOpen}">更多${ui.moreOpen ? ' ▴' : ' ▾'}</button>
      ${ui.moreOpen ? `<div class="dext-more-menu"><button type="button" class="dext-btn" data-cmd="skip"${disabled}>跳过任务</button><button type="button" class="dext-btn" data-cmd="fail"${disabled}>标记失败</button></div>` : ''}
    </div>
    <div class="dext-override"><input class="dext-input" id="dext-override-url" type="url" inputmode="url" aria-label="覆盖目标 URL" placeholder="覆盖目标 URL"><button type="button" class="dext-btn" data-cmd="override"${disabled}>覆盖</button></div>`);

  const dec = state.pendingDecision;
  if (dec) {
    parts.push(`<div class="dext-decision" id="dext-decision"><div class="dext-decision-title">需要确认：${escapeHtml(dec.org_unit_name || dec.id)}</div><div class="dext-decision-actions"><button type="button" class="dext-btn" data-cmd="decision" data-id="${escapeHtml(dec.id)}" data-action="accept">接受建议</button><button type="button" class="dext-btn" data-cmd="decision" data-id="${escapeHtml(dec.id)}" data-action="skip">拒绝</button></div></div>`);
  }
  parts.push(`<div class="dext-footer"><button type="button" class="dext-link-btn" data-cmd="unbind">解除绑定</button></div></div></section>`);
  return parts.join('');
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
    case 'retry_capture': return { kind: 'retry_capture' };
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
  formData?: (id: string) => string | null,
): MountedPanel {
  let shadow: PanelShadowRoot | null = null;
  let lastState: PanelState | null = null;
  const ui: PanelUiState = { collapsed: false, moreOpen: false };
  const readForm = formData ?? (() => null);

  function render(state: PanelState): void {
    lastState = state;
    ensureShadow().innerHTML = renderPanelHtml(state, ui);
  }

  function ensureShadow(): PanelShadowRoot {
    if (shadow) return shadow;
    const host = dom.host;
    if (host.__dextShadow) {
      shadow = host.__dextShadow;
      return shadow;
    }
    host.attachShadowCalls = (host.attachShadowCalls ?? 0) + 1;
    shadow = host.attachShadow({ mode: 'open' });
    host.__dextShadow = shadow;
    shadow.addEventListener('click', (e) => {
      const raw = e.target as { dataset?: Record<string, string>; closest?: (selector: string) => { dataset?: Record<string, string> } | null } | null;
      const hit = raw?.closest?.('[data-cmd],[data-ui]') ?? raw;
      const dataset = hit?.dataset;
      if (!dataset) return;
      if (dataset.ui === 'collapse') {
        ui.collapsed = !ui.collapsed;
        if (lastState) render(lastState);
        return;
      }
      if (dataset.ui === 'more') {
        ui.moreOpen = !ui.moreOpen;
        if (lastState) render(lastState);
        return;
      }
      const cmd = commandFromClick({ dataset }, readForm);
      if (cmd) onCommand?.(cmd);
    });
    return shadow;
  }

  return { render };
}
