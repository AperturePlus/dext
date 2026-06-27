import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeStorage() {
  const counts = new Map();
  const verdicts = new Set();
  const redirects = new Map();
  return {
    async bumpCount(id) { const n = (counts.get(id) ?? 0) + 1; counts.set(id, n); return n; },
    async getCount(id) { return counts.get(id) ?? 0; },
    async markVerdictSent(id) { verdicts.add(id); },
    async wasVerdictSent(id) { return verdicts.has(id); },
    async recordRedirect(id) { redirects.set(id, Date.now()); },
    async shouldRedirect(id) { return true; }, // fake: allow every redirect while below threshold
    async clear(id) { counts.delete(id); verdicts.delete(id); redirects.delete(id); },
  };
}

function fakeApi(statusResponse) {
  const calls = [];
  return {
    calls,
    async getStatus() { return statusResponse; },
    async failJob(id, msg) { calls.push({ kind: 'fail', id, msg }); },
    async skipJob(id, reason) { calls.push({ kind: 'skip', id, reason }); },
  };
}

function fakeChrome() {
  const updates = [];
  const completedCbs = [], errorCbs = [];
  return {
    onNavCompleted(cb) { completedCbs.push(cb); },
    onNavError(cb) { errorCbs.push(cb); },
    async updateTabUrl(tabId, url) { updates.push({ tabId, url }); },
    async findOwnerTab() { return 1; },
    registerWatchdogAlarm() {},
    // test helpers
    updates,
    async fireCompleted(e) { for (const cb of completedCbs) await cb(e); },
    async fireError(e) { for (const cb of errorCbs) await cb(e); },
  };
}

test('404 posts skip with reason=not_found', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0 });
    assert.deepEqual(api.calls, [{ kind: 'skip', id: 'job-1', reason: 'not_found' }]);
  } finally {
    await cleanup();
  }
});

test('502 counts; on 3rd posts fail message=gateway_5xx; does NOT redirect', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    assert.deepEqual(api.calls, []); // not yet
    assert.equal(chr.updates.length, 0); // gateway never self-redirects
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    assert.deepEqual(api.calls, [{ kind: 'fail', id: 'job-1', msg: 'gateway_5xx' }]);
  } finally {
    await cleanup();
  }
});

test('nav_error self-redirects on counts 1 and 2; posts fail nav_error on 3rd', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireError({ tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0 });
    assert.equal(chr.updates.length, 1);
    assert.equal(chr.updates[0].url, 'https://x.edu.cn/p');
    await chr.fireError({ tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0 });
    assert.equal(chr.updates.length, 2); // debounced by storage.shouldRedirect — fake allows every time
    assert.deepEqual(api.calls, []);
    await chr.fireError({ tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0 });
    assert.deepEqual(api.calls, [{ kind: 'fail', id: 'job-1', msg: 'nav_error:ERR_CONNECTION_REFUSED' }]);
  } finally {
    await cleanup();
  }
});

test('no current_job → no calls (backend already released slot)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: null, frontend_health: { alive: false, last_seen_seconds_ago: null } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 502, frameId: 0 });
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});

test('verdict_sent guard: no double report for same job', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0 });
    // a second 404 for the same job (e.g. userscript hadn't navigated away yet) must not re-skip
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0 });
    assert.equal(api.calls.length, 1);
  } finally {
    await cleanup();
  }
});

test('2xx → no action', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 200, frameId: 0 });
    assert.deepEqual(api.calls, []);
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('429 → no action (backend handles rate-limit via status_code path)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/p' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://x.edu.cn/p', statusCode: 429, frameId: 0 });
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});

test('abnormal nav on an UNRELATED url does not touch the in-flight job (spec §4.4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    // The owner browses a different page that 404s. The in-flight crawl job (job-1)
    // must NOT be skipped — the event URL does not match current_job.url.
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/faculty' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireCompleted({ tabId: 1, url: 'https://other.example.com/gone', statusCode: 404, frameId: 0 });
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});

test('nav_error on an UNRELATED url does not redirect to the job (spec §4.4)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const api = fakeApi({ current_job: { id: 'job-1', url: 'https://x.edu.cn/faculty' }, frontend_health: { alive: true, last_seen_seconds_ago: 1 } });
    const chr = fakeChrome();
    const nm = mod.createNavMonitor({ chrome: chr, api, storage: fakeStorage() });
    nm.start();
    await chr.fireError({ tabId: 1, url: 'https://dead.example.invalid/x', error: 'ERR_NAME_NOT_RESOLVED', frameId: 0 });
    assert.equal(chr.updates.length, 0);
    assert.deepEqual(api.calls, []);
  } finally {
    await cleanup();
  }
});
