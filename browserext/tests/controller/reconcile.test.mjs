import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function freshState(overrides = {}) {
  return {
    boundTabId: null, boundAt: null, connected: false, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 0, navigation: null, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
    ...overrides,
  };
}

function job(id, url = `https://x.edu.cn/${id}`) {
  return {
    id, url, status: 'assigned',
    context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
    created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
  };
}

test('backend releases job (current_job null) → clear cache to idle', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ currentJob: job('job-1'), phase: 'assigned', phaseStartedAt: 100 });
    mod.applyReconcile(s, null, 5000);
    assert.equal(s.currentJob, null);
    assert.equal(s.phase, 'idle');
    assert.equal(s.phaseStartedAt, 5000);
    assert.equal(s.navigation, null);
    assert.equal(s.pendingRpc, null);
    assert.equal(s.lastError, null);
  } finally {
    await cleanup();
  }
});

test('backend releases job when already idle + no cache → no-op', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ phase: 'idle', phaseStartedAt: 100 });
    const before = JSON.stringify(s);
    mod.applyReconcile(s, null, 5000);
    assert.equal(JSON.stringify(s), before, 'no mutation when nothing to clear');
  } finally {
    await cleanup();
  }
});

test('new job id → cache job, discard stale nav/pendingRpc, phase=assigned', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    // simulate stale signals from a previous job that the controller had reached (slice 3/4 will set these)
    const staleNav = { jobId: 'old', requestedUrl: 'https://x.edu.cn/old', issuedAt: 10, attempt: 2, kind: 'navigate' };
    const s = freshState({
      currentJob: job('old'), phase: 'navigating', phaseStartedAt: 10,
      navigation: staleNav, pendingRpc: { id: 'rpc-old', jobId: 'old', op: 'capture', sourceDocumentId: 'D1', delivery: 'received', issuedAt: 11, resultDeadlineAt: 41000 },
      lastError: { kind: 'nav_error', error: 'ERR_X' },
    });
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'assigned');
    assert.equal(s.phaseStartedAt, 5000);
    assert.equal(s.navigation, null, 'stale navigation discarded on job change');
    assert.equal(s.pendingRpc, null, 'stale pendingRpc discarded on job change');
    assert.equal(s.lastError, null, 'stale error discarded on job change');
  } finally {
    await cleanup();
  }
});

test('first job from empty state → cache + assigned (boundTabId null keeps assigned, waiting for bind)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ phase: 'idle' });
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'assigned');
  } finally {
    await cleanup();
  }
});

test('same job id → keep in-flight navigation/pendingRpc (do NOT clobber current attempt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const nav = { jobId: 'job-1', requestedUrl: 'https://x.edu.cn/job-1', issuedAt: 10, attempt: 1, kind: 'navigate' };
    const rpc = { id: 'rpc-1', jobId: 'job-1', op: 'capture', sourceDocumentId: 'D1', delivery: 'received', issuedAt: 11, resultDeadlineAt: 41000 };
    const s = freshState({
      currentJob: job('job-1'), phase: 'capturing', phaseStartedAt: 10,
      navigation: nav, pendingRpc: rpc,
    });
    const before = JSON.stringify(s);
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(JSON.stringify(s), before, 'same job id → no mutation; in-flight signals preserved');
  } finally {
    await cleanup();
  }
});

test('does not touch boundTabId / autoMode / paused / connected / backoff fields', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/reconcile.ts', 'reconcile.ts');
  try {
    const s = freshState({ boundTabId: 7, boundAt: 1000, autoMode: true, paused: false, connected: true, backendFailureCount: 3, nextBackendRetryAt: 9999 });
    mod.applyReconcile(s, job('job-1'), 5000);
    assert.equal(s.boundTabId, 7);
    assert.equal(s.boundAt, 1000);
    assert.equal(s.autoMode, true);
    assert.equal(s.paused, false);
    assert.equal(s.connected, true, 'reconcile does not set connected (controller owns that)');
    assert.equal(s.backendFailureCount, 3, 'reconcile does not touch backoff (controller owns that)');
    assert.equal(s.nextBackendRetryAt, 9999);
  } finally {
    await cleanup();
  }
});
