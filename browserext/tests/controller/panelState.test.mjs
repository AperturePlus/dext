import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function state(o = {}) {
  return {
    boundTabId: 42, boundAt: 1000, connected: true, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 1000, navigation: null, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null, pendingDecision: null, ...o,
  };
}

test('buildPanelState: bound tab sender → isBoundTab true, full state', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state({ phase: 'navigating', navigation: { attempt: 2 } }), { tabId: 42 });
    assert.equal(ps.isBoundTab, true);
    assert.equal(ps.bound, true);
    assert.equal(ps.phase, 'navigating');
    assert.equal(ps.navigationAttempt, 2);
  } finally { await cleanup(); }
});

test('buildPanelState: non-bound tab sender → isBoundTab false, bound still true (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state(), { tabId: 99 });
    assert.equal(ps.isBoundTab, false);
    assert.equal(ps.bound, true);
  } finally { await cleanup(); }
});

test('buildPanelState: null tabId sender (no tab) → isBoundTab false', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state(), { tabId: null });
    assert.equal(ps.isBoundTab, false);
  } finally { await cleanup(); }
});

test('buildPanelState: unbound controller → bound false (any tab may bind)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const ps = mod.buildPanelState(state({ boundTabId: null, boundAt: null }), { tabId: 99 });
    assert.equal(ps.bound, false);
    assert.equal(ps.isBoundTab, false);
  } finally { await cleanup(); }
});

test('buildPanelState: lastError is the raw ControllerError, not a string (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const err = { kind: 'rate_limited' };
    const ps = mod.buildPanelState(state({ lastError: err }), { tabId: 42 });
    assert.deepEqual(ps.lastError, err);
  } finally { await cleanup(); }
});

test('buildPanelState: pendingDecision carried through', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/panelState.ts', 'panelState.ts');
  try {
    const dec = { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 3, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' };
    const ps = mod.buildPanelState(state({ pendingDecision: dec }), { tabId: 42 });
    assert.equal(ps.pendingDecision?.id, 'dec-1');
  } finally { await cleanup(); }
});
