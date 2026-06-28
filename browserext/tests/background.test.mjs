import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('wireBackground constructs navMonitor + controller (no watchdog), starts navMonitor', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  // wireBackground reads the global chrome.storage.local directly to build the
  // Controller's persistence area; provide a minimal in-memory fake for the test.
  const store = new Map();
  globalThis.chrome = globalThis.chrome ?? {};
  globalThis.chrome.storage = globalThis.chrome.storage ?? {};
  const prevLocal = globalThis.chrome.storage.local;
  globalThis.chrome.storage.local = {
    async get(keys) {
      const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]);
      const obj = {}; for (const k of arr) if (store.has(k)) obj[k] = store.get(k); return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
  try {
    let started = { nav: false };
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; },
      async getTab() { return null; },
      registerAlarm() {},
    };
    const fakeApi = { async getStatus() { return null; }, async failJob() {}, async skipJob() {}, async sendHeartbeat() {} };
    const fakeStorage = {
      async bumpCount() { return 0; }, async getCount() { return 0; },
      async markVerdictSent() {}, async wasVerdictSent() { return false; },
      async recordRedirect() {}, async shouldRedirect() { return true; }, async clear() {},
    };
    const { navMonitor, controller } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi, storage: fakeStorage });
    // watchdog is gone from the return shape (deleted in slice 2)
    assert.equal('watchdog' in { navMonitor, controller }, false, 'no watchdog in wireBackground return');
    // monkeypatch start to detect invocation
    const origNavStart = navMonitor.start.bind(navMonitor);
    navMonitor.start = () => { started.nav = true; origNavStart(); };
    navMonitor.start();
    assert.equal(started.nav, true);
    assert.ok(controller, 'controller constructed');
    assert.equal(typeof controller.tick, 'function');
    assert.equal(typeof controller.bind, 'function');
  } finally {
    if (prevLocal === undefined) delete globalThis.chrome.storage.local;
    else globalThis.chrome.storage.local = prevLocal;
    await cleanup();
  }
});
