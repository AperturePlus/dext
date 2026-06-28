import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function fakeArea() {
  const store = new Map();
  return {
    async get(keys) {
      const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]);
      const obj = {}; for (const k of arr) if (store.has(k)) obj[k] = store.get(k); return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
}

// The test harness injects EXCLUSIVE_CONTROL_ENABLED = true (see harness.mjs define),
// so these tests assert the GATED-ON path. The build.test.mjs asserts the default
// official build is gated OFF.

test('bind sets boundTabId/phase=assigned and persists (gate ON)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area) });
    await c.bind(42, 1000);
    const s = await c.getState();
    assert.equal(s.boundTabId, 42);
    assert.equal(s.boundAt, 1000);
    assert.equal(s.phase, 'assigned');
    // persisted
    const persisted = (await area.get('dext_controller_state_v1')).dext_controller_state_v1;
    assert.equal(persisted.boundTabId, 42);
  } finally { await cleanup(); }
});

test('tick with no currentJob and idle does not navigate or claim (gate ON, slice-1 no-op)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area) });
    await c.tick(2000);
    const s = await c.getState();
    assert.equal(s.phase, 'idle');
    assert.equal(s.currentJob, null);
  } finally { await cleanup(); }
});

test('initial state is unbound idle', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(fakeArea()) });
    const s = await c.getState();
    assert.equal(s.boundTabId, null);
    assert.equal(s.phase, 'idle');
  } finally { await cleanup(); }
});

// ---- slice 2: /status reconcile + backoff + heartbeat + tab-validity ----

function fakeApi(statusResponse) {
  const calls = { getStatus: 0, sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async failJob() {}, async skipJob() {},
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}

function fakeChrome(getTabResult) {
  return {
    async getTab() { return getTabResult; },
  };
}

function job(id, url = `https://x.edu.cn/${id}`) {
  return {
    id, url, status: 'assigned',
    context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
    created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
  };
}

test('tick: /status returns a job → connected true, currentJob cached, phase assigned, backoff cleared, heartbeat sent', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/job-1' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    // bind first so heartbeat fires (ownerTabId needs boundTabId)
    await c.bind(42, 1000);
    await c.tick(5000);
    const s = await c.getState();
    assert.equal(s.connected, true);
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'assigned');
    assert.equal(s.backendFailureCount, 0);
    assert.equal(s.nextBackendRetryAt, null);
    assert.equal(api.calls.getStatus, 1);
    assert.equal(api.calls.sendHeartbeat.length, 1);
    assert.equal(api.calls.sendHeartbeat[0].owner_tab_id, 'dext-ext-42-1000');
    assert.equal(api.calls.sendHeartbeat[0].current_job_id, 'job-1');
    assert.equal(api.calls.sendHeartbeat[0].timestamp, 5000);
  } finally { await cleanup(); }
});

test('tick: /status fails (backend down) → connected false, backoff set, phase/page unchanged, NO navigation', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi(null);
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);
    const s = await c.getState();
    assert.equal(s.connected, false);
    assert.equal(s.backendFailureCount, 1);
    assert.equal(s.nextBackendRetryAt, 7000);  // 5000 + 2000 (count=1)
    assert.equal(s.phase, 'assigned', 'phase preserved (never navigate/refresh on backend down)');
    assert.equal(s.boundTabId, 42, 'binding preserved across backend failure');
    // heartbeat still fires (not gated by backoff) — bound + tick
    assert.equal(api.calls.sendHeartbeat.length, 1);
  } finally { await cleanup(); }
});

test('tick: second tick within backoff window skips /status (gated by nextBackendRetryAt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi(null);
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);     // sets nextBackendRetryAt=7000
    await c.tick(6000);     // 6000 < 7000 → skip /status
    assert.equal(api.calls.getStatus, 1, 'second tick within backoff did not call /status');
  } finally { await cleanup(); }
});

test('tick: after backoff expires, /status is called again and success resets backoff', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    // first call down, then up: use a statusResponse that flips
    const responses = [
      null,
      { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } },
    ];
    const api = {
      calls: { getStatus: 0 },
      async getStatus() { return responses[api.calls.getStatus++] ?? responses[responses.length - 1]; },
      async failJob() {}, async skipJob() {}, async sendHeartbeat() {},
    };
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.tick(5000);     // down → nextBackendRetryAt=7000, count=1
    await c.tick(7000);     // 7000 >= 7000 → /status up → reset
    const s = await c.getState();
    assert.equal(api.calls.getStatus, 2);
    assert.equal(s.connected, true);
    assert.equal(s.backendFailureCount, 0);
    assert.equal(s.nextBackendRetryAt, null);
  } finally { await cleanup(); }
});

test('tick: bound tab gone (getTab null) → unbind, keep currentJob as assigned, no fail/skip', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    // seed: bound + a cached job (assigned), then the tab disappears
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    // seed a cached job by one successful tick with a present tab
    await c.tick(2000);
    // now make the tab vanish and rebuild the controller over the SAME area so it
    // rehydrates the bound+assigned state
    const chr2 = fakeChrome(null);
    const c2 = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr2 });
    const heartbeatCountBefore = api.calls.sendHeartbeat.length;
    await c2.tick(5000);
    const s = await c2.getState();
    assert.equal(s.boundTabId, null, 'invalid bound tab → unbind');
    assert.equal(s.boundAt, null);
    assert.equal(s.currentJob.id, 'job-1', 'currentJob cache kept as assigned');
    assert.equal(s.phase, 'assigned', 'kept assigned; NOT auto fail/skip, NOT idle');
    // the post-unbind tick (c2) must add NO heartbeat with the now-invalid owner
    const postUnbindHeartbeats = api.calls.sendHeartbeat.slice(heartbeatCountBefore);
    assert.equal(postUnbindHeartbeats.filter(p => p.owner_tab_id === 'dext-ext-42-1000').length, 0,
      'no heartbeat with the now-invalid owner after unbind');
    assert.equal(postUnbindHeartbeats.length, 0, 'unbound controller sends no heartbeat at all');
  } finally { await cleanup(); }
});

test('tick: no heartbeat when unbound (no api.sendHeartbeat calls)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome(null);
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.tick(5000);   // never bound
    assert.equal(api.calls.sendHeartbeat.length, 0);
    const s = await c.getState();
    // unbound + backend has a job → assigned (waiting for bind), currentJob cached
    assert.equal(s.phase, 'assigned');
    assert.equal(s.currentJob.id, 'job-1');
  } finally { await cleanup(); }
});

test('tick: persists only on change (saveIfChanged) — second identical tick writes nothing', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    let setCount = 0;
    const wrappedArea = {
      async get(keys) { return area.get(keys); },
      async set(obj) { setCount += 1; return area.set(obj); },
      async remove(keys) { return area.remove(keys); },
    };
    const api = fakeApi({ current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome({ id: 42, url: 'https://x.edu.cn/job-1' });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(wrappedArea), api, chrome: chr });
    await c.bind(42, 1000);   // bind writes once
    const afterBind = setCount;
    await c.tick(5000);       // changes state (connected, currentJob) → writes
    const afterFirstTick = setCount;
    assert.ok(afterFirstTick > afterBind, 'first tick changed state → wrote');
    await c.tick(7000);       // same job id, already connected, bound tab present → NO change → NO write
    assert.equal(setCount, afterFirstTick, 'second identical tick wrote nothing (saveIfChanged)');
  } finally { await cleanup(); }
});

test('reconciliation alarm constants are exported', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    assert.equal(mod.RECONCILIATION_ALARM_NAME, 'dext-reconcile');
    assert.equal(mod.RECONCILIATION_PERIOD_MINUTES, 1);
  } finally { await cleanup(); }
});
