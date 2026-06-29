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

// ---- slice 3: claim + navigate + landing + funnel + deadline + deliver/join ----

function fakeApi3({ statusResponse, nextJob }) {
  const calls = { getStatus: 0, claimNextJob: 0, fail: [], skip: [], sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async claimNextJob() {
      calls.claimNextJob += 1;
      if (typeof nextJob === 'function') return nextJob();
      return nextJob ?? null;
    },
    async failJob(id, msg) { calls.fail.push({ id, msg }); },
    async skipJob(id, reason) { calls.skip.push({ id, reason }); },
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}

function fakeChrome3({ tab }) {
  const updates = [];
  return {
    calls: { updates },
    async getTab() { return tab; },
    async updateTabUrl(t, url) { updates.push({ tabId: t, url }); },
  };
}

function fullJob(id, url = `https://xjtu.edu.cn/${id}`) {
  return {
    id, url, status: 'assigned',
    context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
    created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
  };
}

test('claim: idle+bound+auto → claimNextJob → navigate; persists navigation BEFORE updateTabUrl', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: fullJob('job-1') });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);   // idle → claim → assigned → navigate
    const s = await c.getState();
    assert.equal(s.currentJob.id, 'job-1');
    assert.equal(s.phase, 'navigating');
    assert.ok(s.navigation, 'navigation persisted BEFORE updateTabUrl');
    assert.equal(s.navigation.attempt, 1);
    assert.equal(s.navigation.kind, 'navigate');
    assert.equal(s.navigation.requestedUrl, 'https://xjtu.edu.cn/job-1');
    assert.deepEqual(chr.calls.updates, [{ tabId: 42, url: 'https://xjtu.edu.cn/job-1' }]);
  } finally { await cleanup(); }
});

test('claim: 204 (no job) → stays idle, no navigate', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    const s = await c.getState();
    assert.equal(s.phase, 'idle');
    assert.equal(s.currentJob, null);
    assert.equal(chr.calls.updates.length, 0);
  } finally { await cleanup(); }
});

test('claim does NOT fire when !autoMode (defaults false)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: fullJob('job-1') });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);   // autoMode defaults false
    await c.tick(5000);
    assert.equal(api.calls.claimNextJob, 0);
    assert.equal(chr.calls.updates.length, 0);
  } finally { await cleanup(); }
});

test('deliverCommitted+Http+PageReady (same requestId/documentId, ok) → landed', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: fullJob('job-1') });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    assert.equal(c.getNavScope().boundTabId, 42);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const s = await c.getState();
    assert.equal(s.phase, 'landed');
    assert.equal(s.navigation.acceptedUrl, 'https://xjtu.edu.cn/job-1');
  } finally { await cleanup(); }
});

test('deliverHttpEvent 429 → immediate fail rate_limited (amend §8.1 #12)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 429, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'rate_limited');
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'rate_limited' });
    assert.equal(s.navigation, null);
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'rate_limited' }]);
    assert.equal(chr.calls.updates.length, 1, 'no re-navigate on 429 (only the initial navigate)');
  } finally { await cleanup(); }
});

test('deliverHttpEvent 502 attempt 1 → retry_navigate (clears signals, attempt 2, updateTabUrl again)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'gateway');
    const s = await c.getState();
    assert.equal(s.phase, 'navigating');
    assert.equal(s.navigation.attempt, 2);
    assert.equal(s.navigation.requestId, undefined, 'requestId cleared on retry');
    assert.equal(s.navigation.commit, undefined, 'commit cleared on retry');
    assert.equal(chr.calls.updates.length, 2, 're-navigated');
  } finally { await cleanup(); }
});

test('deliverHttpEvent 502 attempt 3 → fail gateway_5xx (signal-clear + budget)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'gateway');  // attempt 1 → retry 2
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-2', timeStamp: 6100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-2', timeStamp: 6200 }, 'gateway');  // attempt 2 → retry 3
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-3', timeStamp: 7100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-3', timeStamp: 7200 }, 'gateway');  // attempt 3 → fail
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'gateway_5xx' });
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'gateway_5xx' }]);
  } finally { await cleanup(); }
});

test('deliverHttpEvent 404 → immediate skip not_found (no budget)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 404, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'not_found');
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError, null, 'skip does not set a fail error');
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'not_found' }]);
  } finally { await cleanup(); }
});

test('deliverBeforeRedirect offsite → terminal skip offsite_redirect (amend §8.1 #14)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverBeforeRedirect({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', redirectUrl: 'https://attacker.edu.cn/x', requestId: 'REQ-1', timeStamp: 5200 });
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'offsite_redirect' }]);
  } finally { await cleanup(); }
});

test('deliverBeforeRedirect onsite → no skip; keep waiting for onCompleted (amend §8.1 #14)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverBeforeRedirect({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', redirectUrl: 'https://xjtu.edu.cn/renxueguang', requestId: 'REQ-1', timeStamp: 5200 });
    const s = await c.getState();
    assert.equal(s.phase, 'navigating', 'onsite redirect keeps waiting');
    assert.deepEqual(api.calls.skip, []);
  } finally { await cleanup(); }
});

test('deliverHttpEvent with documentId !== commit.documentId → discarded (amend §8.1 #7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-OTHER', timeStamp: 5300 }, 'ok');
    const s = await c.getState();
    assert.equal(s.navigation.http, undefined, 'http with mismatched documentId discarded');
    assert.equal(s.phase, 'navigating', 'still waiting');
  } finally { await cleanup(); }
});

test('stale requestId from previous attempt does NOT pollute current attempt (amend §8.1 #6)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 5200 }, 'gateway');  // attempt 1 fail → retry
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-2', timeStamp: 6100 });
    // a LATE 502 from the OLD requestId REQ-1 arrives → must be discarded
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 502, frameId: 0, requestId: 'REQ-1', timeStamp: 6200 }, 'gateway');
    const s = await c.getState();
    assert.equal(s.navigation.requestId, 'REQ-2', 'current attempt keeps REQ-2');
    assert.equal(s.navigation.attempt, 2, 'not bumped to 3 by the stale event');
    assert.equal(s.navigation.http, undefined, 'stale http not written');
  } finally { await cleanup(); }
});

test('deliverError ERR_CONNECTION_REFUSED attempt 3 → fail nav_error:ERR_…', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    for (const [req, t] of [['REQ-1',5100],['REQ-2',6100],['REQ-3',7100]]) {
      await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: req, timeStamp: t });
      await c.deliverError({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', error: 'ERR_CONNECTION_REFUSED', frameId: 0, requestId: req, timeStamp: t + 100 }, 'nav_error');
    }
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'nav_error', error: 'ERR_CONNECTION_REFUSED' });
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'nav_error:ERR_CONNECTION_REFUSED' }]);
  } finally { await cleanup(); }
});

test('navigation deadline (never landed, 30s no commit) → funnel retry then fail nav_error:timeout at attempt 3', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);   // issuedAt=5000, attempt 1
    await c.tick(36000);   // 5000+30000=35000; 36000>=35000 expired, no commit → never_landed → retry attempt 2
    let s = await c.getState();
    assert.equal(s.navigation.attempt, 2);
    assert.equal(chr.calls.updates.length, 2);
    await c.tick(66000);   // attempt 2 expired → attempt 3
    s = await c.getState();
    assert.equal(s.navigation.attempt, 3);
    await c.tick(96000);   // attempt 3 expired → fail nav_error:timeout
    s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(s.lastError, { kind: 'nav_error', error: 'timeout' });
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'nav_error:timeout' }]);
  } finally { await cleanup(); }
});

test('landing deadline (commit present, no PAGE_READY, 30s) → content_unavailable/page_ready, NO refresh', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });   // committedAt=5200
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.tick(36000);   // 5200+30000=35200; 36000>=35200 expired, no PAGE_READY → content_unavailable/page_ready
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError.kind, 'content_unavailable');
    assert.equal(s.lastError.missing, 'page_ready');
    assert.equal(s.lastError.sourceDocumentId, 'DOC-1');
    assert.equal(s.lastError.recoveryExhausted, true);
    assert.equal(chr.calls.updates.length, 1, 'NEVER refreshed on content_unavailable');
  } finally { await cleanup(); }
});

test('deliver* ignores events from a non-bound tab (scope guard)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' } });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await c.bind(42, 1000);
    await c.setAutoMode(true);
    await c.tick(5000);
    await c.deliverBeforeRequest({ tabId: 999, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-X', timeStamp: 5100 });
    const s = await c.getState();
    assert.equal(s.navigation.requestId, undefined, 'non-bound-tab event discarded');
  } finally { await cleanup(); }
});

test('deliver* does not throw on an unbound controller (no navigation to correlate)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi3({ statusResponse: { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } }, nextJob: null });
    const chr = fakeChrome3({ tab: null });
    const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
    await assert.doesNotReject(async () => {
      await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'u', timeStamp: 5000 });
      await c.deliverHttpEvent({ tabId: 42, url: 'u', statusCode: 200, frameId: 0, requestId: 'R', timeStamp: 5000 }, 'ok');
      await c.deliverPageReady({ documentId: 'DOC-1', url: 'u', detection: { errorPage: false, terminalReason: null }, timeStamp: 5000 });
    });
    assert.deepEqual(api.calls.fail, []);
    assert.deepEqual(api.calls.skip, []);
  } finally { await cleanup(); }
});

// ---- slice 4: capture dispatch + CAPTURE_RESULT + recovery + complete ----

function fakeChrome4() {
  const updates = [];
  const sent = [];
  return {
    calls: { updates, sent },
    async getTab() { return { id: 42, url: 'https://xjtu.edu.cn/job-1' }; },
    async updateTabUrl(t, url) { updates.push({ tabId: t, url }); },
    async sendMessage(tabId, message, options) { sent.push({ tabId, message, options }); return { received: true }; },
  };
}
function fakeApi4({ statusResponse, completeOk = true }) {
  const calls = { getStatus: 0, claimNextJob: 0, complete: [], fail: [], skip: [], sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async claimNextJob() { return null; },
    async completeJob(id, html, url, title, ps) { calls.complete.push({ id, html, url, title, ps }); },
    async failJob(id, msg) { calls.fail.push({ id, msg }); },
    async skipJob(id, reason) { calls.skip.push({ id, reason }); },
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}
function landedController(mod, area) {
  // build a controller already navigated to landed on job-1, doc DOC-1.
  // NOTE: the brief's helper omits setAutoMode(true), but navigateNow (step 5)
  // and the claim gate both require autoMode — without it the controller stays
  // 'assigned' and never navigates. Mirror the slice-3 landing-test pattern.
  const api = fakeApi4({ statusResponse: { current_job: fullJob('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
  const chr = fakeChrome4();
  const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
  return { c, api, chr, area, async ready() { await c.bind(42, 1000); await c.setAutoMode(true); await c.tick(5000); } };
}

test('landed → dispatches CAPTURE to commit.documentId with a stable rpcId; pendingRpc.delivery=prepared BEFORE send, received AFTER (amend §1.1, §4.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const s = await c.getState();
    assert.equal(s.phase, 'capturing', 'landed → capturing');
    assert.ok(s.pendingRpc, 'pendingRpc persisted');
    assert.equal(s.pendingRpc.op, 'capture');
    assert.equal(s.pendingRpc.sourceDocumentId, 'DOC-1');
    assert.equal(s.pendingRpc.delivery, 'received', 'delivery promoted to received after send resolves');
    assert.equal(chr.calls.sent.length, 1);
    assert.equal(chr.calls.sent[0].options.documentId, 'DOC-1', 'delivered to precise documentId');
    assert.equal(chr.calls.sent[0].message.op, 'CAPTURE');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT ok:true → complete + clear pendingRpc → idle (capture success)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, api, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: true, url: 'https://xjtu.edu.cn/job-1', html: '<html/>', title: 'T', paginationStates: [], detection: { errorPage: false, terminalReason: null } }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-1' });
    assert.deepEqual(api.calls.complete, [{ id: 'job-1', html: '<html/>', url: 'https://xjtu.edu.cn/job-1', title: 'T', ps: [] }]);
    const s = await c.getState();
    assert.equal(s.pendingRpc, null);
    assert.equal(s.navigation, null);
    assert.equal(s.phase, 'idle');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT second-detection fail (errorPage, navigate kind) → gateway funnel re-navigate (amend §2.3, §8.1 #18)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: false, url: 'u', detection: { errorPage: true, terminalReason: null }, error: 'error_page' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-1' });
    const s = await c.getState();
    assert.equal(s.pendingRpc, null, 'rpc cleared');
    assert.equal(s.phase, 'navigating', 'exited capturing → gateway funnel re-navigate');
    assert.equal(s.navigation.attempt, 2);
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT second-detection terminal not_found → skip not_found (amend §8.1 #18)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, api, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: false, url: 'u', detection: { errorPage: false, terminalReason: 'not_found' }, error: 'not_found' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-1' });
    assert.deepEqual(api.calls.skip, [{ id: 'job-1', reason: 'not_found' }]);
    const s = await c.getState();
    assert.equal(s.phase, 'error');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT with mismatched documentId → discarded (late/stale RPC, no complete) (amend §4.1, §8.1 #10)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, api, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpc = (await c.getState()).pendingRpc;
    // wrong documentId (a new document's stale result)
    await c.deliverCaptureResult({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: true, url: 'u', html: 'x', title: 't', detection: { errorPage: false, terminalReason: null } }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-OTHER' });
    assert.deepEqual(api.calls.complete, [], 'mismatched documentId result discarded, no complete');
    assert.equal((await c.getState()).phase, 'capturing', 'still capturing');
  } finally { await cleanup(); }
});

test('capture RPC recovery: result deadline expired + source doc still matches → re-send SAME rpcId (amend §6.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const rpcBefore = (await c.getState()).pendingRpc;
    assert.equal(chr.calls.sent.length, 1);
    // advance past resultDeadlineAt (issuedAt≈5400, +30s = 35400) and past 5s recovery gap
    await c.tick(40000);
    const s = await c.getState();
    assert.equal(chr.calls.sent.length, 2, 're-sent CAPTURE');
    assert.equal(s.pendingRpc.id, rpcBefore.id, 'SAME rpcId re-used');
    assert.equal(s.pendingRpc.recoveryAttempts, 1);
  } finally { await cleanup(); }
});

test('capture RPC recovery exhausted (3 re-sends) → content_unavailable/capture_result, no more auto RPC (amend §6.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const { c, chr, ready } = landedController(mod, area);
    await ready();
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 5100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 5200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 5300 }, 'ok');
    await c.deliverPageReady({ documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
    const sentBefore = chr.calls.sent.length;
    // three recovery windows (each must clear the 5s gap): issueAt≈5400+30s=35400; gaps at 35400+5, then +35, then +65
    await c.tick(40000);  // re-send #1
    await c.tick(45000);  // re-send #2
    await c.tick(50000);  // re-send #3 (now recoveryAttempts==3, exhausted)
    await c.tick(55000);  // NO further send
    const s = await c.getState();
    assert.equal(chr.calls.sent.length, sentBefore + 3, 'exactly 3 re-sends');
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError.kind, 'content_unavailable');
    assert.equal(s.lastError.missing, 'capture_result');
    assert.equal(s.lastError.recoveryExhausted, true);
  } finally { await cleanup(); }
});

// ---- slice 4: form-action prepare→persist→perform→confirm + failure routing ----

async function landOnList(mod, area, api, chr) {
  const c = mod.createCrawlController({ storage: mod.createControllerStorage(area), api, chrome: chr });
  await c.bind(42, 1000);
  await c.setAutoMode(true);
  await c.tick(5000);
  await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/list', requestId: 'REQ-1', timeStamp: 5100 });
  await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-LIST', url: 'https://xjtu.edu.cn/list', timeStamp: 5200 });
  await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/list', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-LIST', timeStamp: 5300 }, 'ok');
  await c.deliverPageReady({ documentId: 'DOC-LIST', url: 'https://xjtu.edu.cn/list', detection: { errorPage: false, terminalReason: null }, timeStamp: 5400 });
  return c;
}
function formActionJob(id = 'job-1') {
  const j = fullJob(id, 'https://xjtu.edu.cn/list');
  j.action = { kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true };
  return j;
}
function fakeApi4b({ statusResponse }) {
  const calls = { getStatus: 0, complete: [], fail: [], skip: [], sendHeartbeat: [] };
  return {
    calls,
    async getStatus() { calls.getStatus += 1; return statusResponse; },
    async claimNextJob() { return null; },
    async completeJob(id, html, url, title, ps) { calls.complete.push({ id, html, url, title, ps }); },
    async failJob(id, msg) { calls.fail.push({ id, msg }); },
    async skipJob(id, reason) { calls.skip.push({ id, reason }); },
    async sendHeartbeat(p) { calls.sendHeartbeat.push(p); },
  };
}

test('ACTION_PREPARED ok:true → persists NEW form_action NavigationState (sourceDoc copied, slots absent) + PERFORM_ACTION dispatched (amend §5.2, §8.1 #15)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    // dispatch PREPARE_ACTION (Controller-initiated on landed? — for test we drive via a helper)
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const s0 = await c.getState();
    const prepRpc = s0.pendingRpc;
    assert.equal(prepRpc.op, 'prepare_action');
    assert.equal(prepRpc.sourceDocumentId, 'DOC-LIST');
    // CS returns ACTION_PREPARED ok:true
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list?PAGENUM=3', method: 'GET', expectedEffect: 'same_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const s = await c.getState();
    assert.equal(s.phase, 'acting');
    assert.equal(s.navigation.kind, 'form_action');
    assert.equal(s.navigation.requestedUrl, 'https://xjtu.edu.cn/list?PAGENUM=3');
    assert.equal(s.navigation.sourceDocumentId, 'DOC-LIST');
    assert.equal(s.navigation.requestId, undefined, 'requestId absent (new navigation)');
    assert.equal(s.navigation.commit, undefined, 'commit absent');
    assert.equal(s.navigation.http, undefined, 'http absent');
    assert.equal(s.navigation.action.preparationFingerprint, 'FP1');
    assert.equal(s.pendingRpc.op, 'perform_action');
    assert.equal(chr.calls.sent.at(-1).message.op, 'PERFORM_ACTION');
    assert.equal(chr.calls.sent.at(-1).options.documentId, 'DOC-LIST');
  } finally { await cleanup(); }
});

test('ACTION_PREPARED ok:false → retries prepare (same rpcId) then fails form_action_prepare_failed (no tabs.update) (amend §5.1, §6.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    for (let i = 0; i < 3; i++) {
      await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: false, error: 'form_not_found' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
      // advance past the 5s recovery gap between retries
      await c.tick(60000 + i * 10000);
    }
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.deepEqual(api.calls.fail, [{ id: 'job-1', msg: 'form_action_prepare_failed:form_not_found' }]);
    assert.equal(chr.calls.updates.length, 1, 'only the initial list navigate; no tabs.update for form failure');
  } finally { await cleanup(); }
});

test('same-document effect: ACTION_RESULT effectApplied+!navigationExpected → landed → capture (amend §5.5, §8.1 #5)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list?PAGENUM=3', method: 'GET', expectedEffect: 'same_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const perfRpc = (await c.getState()).pendingRpc;
    await c.deliverActionResult({ op: 'ACTION_RESULT', rpcId: perfRpc.id, jobId: 'job-1', ok: true, invoked: true, navigationExpected: false, effectApplied: true }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const s = await c.getState();
    assert.equal(s.phase, 'capturing', 'same-document effect confirmed → landed → capturing');
    assert.equal(s.navigation.action.effectConfirmed, true);
    assert.equal(s.pendingRpc.op, 'capture');
  } finally { await cleanup(); }
});

test('navigationExpected:true ACTION_RESULT does NOT capture (waits new-document landing) (amend §5.4, §8.1 #4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list', method: 'POST', expectedEffect: 'new_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const perfRpc = (await c.getState()).pendingRpc;
    await c.deliverActionResult({ op: 'ACTION_RESULT', rpcId: perfRpc.id, jobId: 'job-1', ok: true, invoked: true, navigationExpected: true, effectApplied: false }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const s = await c.getState();
    assert.equal(s.phase, 'acting', 'still acting, not capturing');
    assert.equal(s.navigation.action.invocationReported, true);
  } finally { await cleanup(); }
});

test('form-action 30s no commit/effect → fail form_action_navigation_failed (NO tabs.update, NO re-submit) (amend §5.6, §8.1 #11)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list', method: 'POST', expectedEffect: 'new_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const updatesBefore = chr.calls.updates.length;
    const sentBefore = chr.calls.sent.length;
    await c.tick(65000);   // issuedAt≈5500 +30s=35500 well past; no commit/effect
    const s = await c.getState();
    assert.equal(s.phase, 'error');
    assert.equal(s.lastError.kind, 'nav_error');
    assert.ok(String(s.lastError.error).startsWith('form_action_navigation_failed'));
    assert.deepEqual(api.calls.fail.map((f) => f.msg), [s.lastError.error]);
    assert.equal(chr.calls.updates.length, updatesBefore, 'NO tabs.update on form-action failure');
    assert.equal(chr.calls.sent.length, sentBefore, 'NO re-submit');
  } finally { await cleanup(); }
});

test('PERFORM_ACTION repeat (same rpcId) does NOT generate a new perform rpcId after a correlated requestId appears (amend §5.4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  try {
    const area = fakeArea();
    const api = fakeApi4b({ statusResponse: { current_job: formActionJob(), frontend_health: { alive: true, last_seen_seconds_ago: 1 } } });
    const chr = fakeChrome4();
    const c = await landOnList(mod, area, api, chr);
    await c.dispatchPrepareAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '3' }, submit: true });
    const prepRpc = (await c.getState()).pendingRpc;
    await c.deliverActionPrepared({ op: 'ACTION_PREPARED', rpcId: prepRpc.id, jobId: 'job-1', ok: true, targetUrl: 'https://xjtu.edu.cn/list', method: 'POST', expectedEffect: 'new_document', preparationFingerprint: 'FP1' }, { tab: { id: 42 }, frameId: 0, documentId: 'DOC-LIST' });
    const perfRpcId = (await c.getState()).pendingRpc.id;
    // a correlated main-frame request arrives (binds requestId in acting phase)
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/list', requestId: 'REQ-PERF', timeStamp: 5600 });
    // the perform-recovery tick must NOT create a new rpcId
    await c.tick(60000);
    const s = await c.getState();
    assert.equal(s.pendingRpc.id, perfRpcId, 'no new perform rpcId once requestId is bound');
    assert.equal(s.navigation.requestId, 'REQ-PERF');
  } finally { await cleanup(); }
});
