/** Pure PanelState helpers for the content panel (amend §7). The SW projects
 *  the raw ControllerError; THIS module turns it into human-readable text so the
 *  SW never holds a localized string in PanelState. Also a shallow equality helper
 *  to skip no-op re-renders on identical STATE_CHANGED payloads. No chrome, no DOM. */

import type { ControllerError } from '../shared/state.js';
import type { PanelState } from '../shared/rpc.js';

const CONTENT_UNAVAILABLE_LABELS: Record<string, string> = {
  page_ready: 'page_ready',
  capture_result: 'capture_result',
  action_prepare: 'action_prepare',
  action_result: 'action_result',
  http_outcome: 'http_outcome',
};

export function formatError(e: ControllerError | null): string {
  if (e === null) return '';
  switch (e.kind) {
    case 'nav_error': return `nav_error:${e.error}`;
    case 'gateway_5xx': return 'gateway_5xx';
    case 'rate_limited': return 'rate_limited';
    case 'unexpected_status': return `unexpected_status:${e.statusCode}`;
    case 'content_unavailable':
      return `content_unavailable:${CONTENT_UNAVAILABLE_LABELS[e.missing] ?? e.missing}（需人工处理）`;
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
