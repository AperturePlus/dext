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
