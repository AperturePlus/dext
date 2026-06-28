import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function state(overrides = {}) {
  return {
    boundTabId: null, boundAt: null, connected: false, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 0, navigation: null, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
    ...overrides,
  };
}

test('ownerTabIdFor: null when unbound; deterministic dext-ext-<tab>-<boundAt> when bound', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    assert.equal(mod.ownerTabIdFor(state()), null);
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: 7, boundAt: null })), null);
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: null, boundAt: 1000 })), null);
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: 7, boundAt: 1000 })), 'dext-ext-7-1000');
    // stable: same persisted fields → same id (rehydration across SW restarts)
    assert.equal(mod.ownerTabIdFor(state({ boundTabId: 7, boundAt: 1000 })), 'dext-ext-7-1000');
  } finally {
    await cleanup();
  }
});

test('send is a no-op when unbound (no owner to heartbeat)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    const calls = [];
    const hb = mod.createHeartbeat({ async sendHeartbeat(p) { calls.push(p); } });
    await hb.send(state({ currentJob: { id: 'job-1', url: 'https://x.edu.cn/p' } }), 5000);
    assert.equal(calls.length, 0);
  } finally {
    await cleanup();
  }
});

test('send posts the full payload with stable ownerTabId when bound', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    const calls = [];
    const hb = mod.createHeartbeat({ async sendHeartbeat(p) { calls.push(p); } });
    await hb.send(state({
      boundTabId: 42, boundAt: 1000, autoMode: true, paused: false,
      currentJob: { id: 'job-1', url: 'https://x.edu.cn/p' },
    }), 7777);
    assert.deepEqual(calls, [{
      owner_tab_id: 'dext-ext-42-1000',
      url: 'https://x.edu.cn/p',
      current_job_id: 'job-1',
      auto_mode: true,
      paused: false,
      timestamp: 7777,
    }]);
  } finally {
    await cleanup();
  }
});

test('send uses empty url + null current_job_id when no currentJob (bound but idle)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/heartbeat.ts', 'heartbeat.ts');
  try {
    const calls = [];
    const hb = mod.createHeartbeat({ async sendHeartbeat(p) { calls.push(p); } });
    await hb.send(state({ boundTabId: 42, boundAt: 1000, autoMode: false, paused: true }), 9999);
    assert.deepEqual(calls, [{
      owner_tab_id: 'dext-ext-42-1000',
      url: '',
      current_job_id: null,
      auto_mode: false,
      paused: true,
      timestamp: 9999,
    }]);
  } finally {
    await cleanup();
  }
});
