/** Pure PanelState helpers for the content panel (amend §7). The SW projects
 *  the raw ControllerError; THIS module turns it into human-readable text so the
 *  SW never holds a localized string in PanelState. Also a shallow equality helper
 *  to skip no-op re-renders on identical STATE_CHANGED payloads. No chrome, no DOM. */

import type { ControllerError, ControllerPhase } from '../shared/state.js';
import type { PanelState } from '../shared/rpc.js';

export interface ErrorPresentation {
  title: string;
  description: string;
}

export type StepState = 'pending' | 'active' | 'complete' | 'error';

export interface PanelProgress {
  navigation: StepState;
  capture: StepState;
  submit: StepState;
}

export function describeError(e: ControllerError | null): ErrorPresentation | null {
  if (e === null) return null;
  switch (e.kind) {
    case 'nav_error':
      return { title: '页面导航失败', description: `浏览器未能打开目标页面（${e.error}）。` };
    case 'gateway_5xx':
      return { title: '目标网站暂时不可用', description: '服务器返回网关错误，可以稍后重新打开。' };
    case 'rate_limited':
      return { title: '请求过于频繁', description: '目标网站限制了访问频率，请稍后再试。' };
    case 'unexpected_status':
      return { title: '页面响应异常', description: `目标页面返回了 HTTP ${e.statusCode}。` };
    case 'content_unavailable':
      switch (e.missing) {
        case 'capture_result':
          return { title: '页面捕获未返回', description: '目标页面已加载，但扩展未收到捕获结果。' };
        case 'page_ready':
          return { title: '页面未就绪', description: '目标页面没有及时报告可读取状态。' };
        case 'action_prepare':
          return { title: '页面操作未准备好', description: '扩展无法准备当前页面所需的表单操作。' };
        case 'action_result':
          return { title: '页面操作未完成', description: '扩展没有收到页面操作的执行结果。' };
        case 'http_outcome':
          return { title: '页面响应未确认', description: '扩展无法确认目标页面的网络响应。' };
      }
  }
}

export function formatError(e: ControllerError | null): string {
  const presentation = describeError(e);
  return presentation ? `${presentation.title}：${presentation.description}` : '';
}

export function panelProgress(state: Pick<PanelState, 'phase' | 'lastError'> | { phase: ControllerPhase; lastError: ControllerError | null }): PanelProgress {
  if (state.phase === 'error') {
    const captureError = state.lastError?.kind === 'content_unavailable'
      && state.lastError.missing === 'capture_result';
    return captureError
      ? { navigation: 'complete', capture: 'error', submit: 'pending' }
      : { navigation: 'error', capture: 'pending', submit: 'pending' };
  }
  switch (state.phase) {
    case 'landed':
    case 'capturing':
      return { navigation: 'complete', capture: 'active', submit: 'pending' };
    case 'submitting':
      return { navigation: 'complete', capture: 'complete', submit: 'active' };
    case 'navigating':
    case 'acting':
      return { navigation: 'active', capture: 'pending', submit: 'pending' };
    case 'idle':
    case 'assigned':
    case 'claiming':
      return { navigation: 'active', capture: 'pending', submit: 'pending' };
  }
}

/** Shallow structural equality over the PanelState fields the panel renders.
 *  Used to skip re-render when a STATE_CHANGED carries an unchanged state.
 *  lastError is compared by kind AND its discriminating detail (slice-6 fix:
 *  slice-5 compared kind only, so a nav_error whose `error` string changed,
 *  or an unexpected_status whose statusCode changed, left stale error text on
 *  screen until the next phase change). */
export function panelStateEqual(a: PanelState | undefined, b: PanelState | undefined): boolean {
  if (a === b) return true;
  if (!a || !b) return false;
  return (
    a.isBoundTab === b.isBoundTab &&
    a.bound === b.bound &&
    a.connected === b.connected &&
    a.autoMode === b.autoMode &&
    a.paused === b.paused &&
    a.phase === b.phase &&
    a.navigationAttempt === b.navigationAttempt &&
    a.currentJob?.id === b.currentJob?.id &&
    a.currentJob?.url === b.currentJob?.url &&
    a.currentJob?.context?.university_name === b.currentJob?.context?.university_name &&
    errorEqual(a.lastError, b.lastError) &&
    a.pendingDecision?.id === b.pendingDecision?.id
  );
}

/** Compare two ControllerError values by kind + the kind's discriminating detail.
 *  gateway_5xx/rate_limited carry no detail beyond kind. */
function errorEqual(x: ControllerError | null, y: ControllerError | null): boolean {
  if (x === y) return true;
  if (!x || !y) return false;
  if (x.kind !== y.kind) return false;
  switch (x.kind) {
    case 'nav_error': return x.error === (y as Extract<ControllerError, { kind: 'nav_error' }>).error;
    case 'unexpected_status': return x.statusCode === (y as Extract<ControllerError, { kind: 'unexpected_status' }>).statusCode;
    case 'content_unavailable': return x.missing === (y as Extract<ControllerError, { kind: 'content_unavailable' }>).missing;
    case 'gateway_5xx':
    case 'rate_limited': return true;
  }
}
