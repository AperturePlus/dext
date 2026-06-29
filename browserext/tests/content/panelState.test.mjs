import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('formatError(null) → empty string', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try { assert.equal(mod.formatError(null), ''); } finally { await cleanup(); }
});

test('formatError(nav_error) → nav_error:<error>', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    assert.equal(mod.formatError({ kind: 'nav_error', error: 'ERR_CONNECTION_RESET' }), 'nav_error:ERR_CONNECTION_RESET');
  } finally { await cleanup(); }
});

test('formatError(content_unavailable) → 人工处理 message with missing tag (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    const s = mod.formatError({
      kind: 'content_unavailable', missing: 'page_ready', sourceDocumentId: 'DOC-1',
      since: 1000, recoveryAttempts: 3, nextRecoveryAt: null, recoveryExhausted: true,
    });
    assert.match(s, /page_ready/);
    assert.match(s, /人工/);
  } finally { await cleanup(); }
});

test('formatError(gateway_5xx / rate_limited / unexpected_status) → stable labels', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    assert.equal(mod.formatError({ kind: 'gateway_5xx' }), 'gateway_5xx');
    assert.equal(mod.formatError({ kind: 'rate_limited' }), 'rate_limited');
    assert.equal(mod.formatError({ kind: 'unexpected_status', statusCode: 403 }), 'unexpected_status:403');
  } finally { await cleanup(); }
});

test('panelStateEqual: undefined === undefined; differing phase is unequal', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    const a = { isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false, phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null };
    assert.equal(mod.panelStateEqual(undefined, undefined), true);
    assert.equal(mod.panelStateEqual(a, { ...a, phase: 'navigating' }), false);
    assert.equal(mod.panelStateEqual(a, a), true);
  } finally { await cleanup(); }
});

test('panelStateEqual: same-kind-different-detail lastError is UNEQUAL (slice-6 fix, slice-5 deferred #1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panelState.ts', 'panelState.ts');
  try {
    const base = { isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false, phase: 'navigating', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null };
    // nav_error: same kind, different error string → must be unequal (else stale ERR_CONNECTION_RESET stays on screen).
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_CONNECTION_RESET' } },
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_NAME_NOT_RESOLVED' } },
    ), false);
    // unexpected_status: same kind, different statusCode → unequal.
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'unexpected_status', statusCode: 401 } },
      { ...base, lastError: { kind: 'unexpected_status', statusCode: 403 } },
    ), false);
    // content_unavailable: same kind, different missing → unequal.
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'content_unavailable', missing: 'page_ready', sourceDocumentId: 'D', since: 1, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: false } },
      { ...base, lastError: { kind: 'content_unavailable', missing: 'capture_result', sourceDocumentId: 'D', since: 1, recoveryAttempts: 0, nextRecoveryAt: null, recoveryExhausted: false } },
    ), false);
    // kind-only errors (gateway_5xx / rate_limited): equal when same kind.
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'gateway_5xx' } },
      { ...base, lastError: { kind: 'gateway_5xx' } },
    ), true);
    // same kind AND same detail → equal (regression guard for the deepening).
    assert.equal(mod.panelStateEqual(
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_CONNECTION_RESET' } },
      { ...base, lastError: { kind: 'nav_error', error: 'ERR_CONNECTION_RESET' } },
    ), true);
    // null vs null stays equal; null vs non-null stays unequal.
    assert.equal(mod.panelStateEqual({ ...base, lastError: null }, { ...base, lastError: null }), true);
    assert.equal(mod.panelStateEqual({ ...base, lastError: null }, { ...base, lastError: { kind: 'rate_limited' } }), false);
  } finally { await cleanup(); }
});
