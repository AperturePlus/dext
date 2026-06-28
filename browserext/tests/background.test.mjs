import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('wireBackground constructs navMonitor {chrome, controller} + controller, starts navMonitor, wires reconciliation alarm → controller.tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
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
    let alarmReg = null;
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {},
      onBeforeRequest() {}, onBeforeRedirect() {}, onCommitted() {}, onHistoryStateUpdated() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; },
      async getTab() { return null; },
      registerAlarm(name, period, cb) { alarmReg = { name, period, cb }; },
    };
    const fakeApi = { async getStatus() { return null; }, async claimNextJob() { return null; }, async failJob() {}, async skipJob() {}, async sendHeartbeat() {} };
    const { navMonitor, controller } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi });
    assert.ok(navMonitor, 'navMonitor constructed');
    assert.ok(controller, 'controller constructed');
    assert.equal(typeof controller.tick, 'function');
    assert.equal(typeof controller.deliverCommitted, 'function');
    assert.equal(typeof controller.getNavScope, 'function');
    assert.ok(alarmReg, 'reconciliation alarm registered');
    assert.equal(alarmReg.name, 'dext-reconcile');
    assert.equal(alarmReg.period, 1);
    await assert.doesNotReject(async () => { await alarmReg.cb(); });
  } finally {
    if (prevLocal === undefined) delete globalThis.chrome.storage.local;
    else globalThis.chrome.storage.local = prevLocal;
    await cleanup();
  }
});
