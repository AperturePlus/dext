import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeStorage() {
  const redirects = new Map();
  return {
    async bumpCount() { return 0; },
    async getCount() { return 0; },
    async markVerdictSent() {},
    async wasVerdictSent() { return false; },
    async recordRedirect(id) { redirects.set(id, Date.now()); },
    async shouldRedirect(id) { return !redirects.has(id); },
    async clear() {},
  };
}

function fakeChrome(ownerTabId = 1) {
  const updates = [];
  let alarmCb = null;
  return {
    onNavCompleted() {},
    onNavError() {},
    async updateTabUrl(tabId, url) { updates.push({ tabId, url }); },
    async findOwnerTab() { return ownerTabId; },
    registerWatchdogAlarm(_name, _period, cb) { alarmCb = cb; },
    updates,
    fireAlarm() { if (alarmCb) alarmCb(); },
  };
}

function fakeApi(statusResponse) {
  return {
    async getStatus() { return statusResponse; },
    async failJob() {},
    async skipJob() {},
  };
}

test('tick redirects when stale + current_job present', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 1);
    assert.equal(chr.updates[0].url, 'https://x.edu.cn/p');
  } finally {
    await cleanup();
  }
});

test('tick does nothing when heartbeat alive', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: true, last_seen_seconds_ago: 3 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('tick does nothing when no current_job (backend released slot)', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: null,
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('tick does nothing when stale but within 15s threshold', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    // alive=false but last_seen 10s (< 15s threshold) → don't redirect yet
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 10 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('debounce: second tick for same job within window does not re-redirect', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    await wd.tick();
    assert.equal(chr.updates.length, 1); // storage.shouldRedirect blocked the 2nd
  } finally {
    await cleanup();
  }
});

test('start registers alarm; fireAlarm triggers tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(1);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    wd.start();
    chr.fireAlarm();
    // alarm callback is async; let it settle
    await new Promise((r) => setImmediate(r));
    assert.equal(chr.updates.length, 1);
  } finally {
    await cleanup();
  }
});

test('no owner tab found → no redirect, no crash', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    const api = fakeApi({
      current_job: { id: 'job-1', url: 'https://x.edu.cn/p' },
      frontend_health: { alive: false, last_seen_seconds_ago: 20 },
    });
    const chr = fakeChrome(null);
    const wd = mod.createWatchdog({ chrome: chr, api, storage: fakeStorage() });
    await wd.tick();
    assert.equal(chr.updates.length, 0);
  } finally {
    await cleanup();
  }
});

test('STALE_THRESHOLD_SECONDS is 15', async () => {
  const { mod, cleanup } = await importTsModule('../src/watchdog.ts', 'watchdog.ts');
  try {
    assert.equal(mod.STALE_THRESHOLD_SECONDS, 15);
    assert.equal(mod.WATCHDOG_ALARM_NAME, 'dext-watchdog');
    assert.equal(mod.WATCHDOG_PERIOD_MINUTES, 1);
  } finally {
    await cleanup();
  }
});
