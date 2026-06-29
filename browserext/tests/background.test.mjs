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
      async updateTabUrl() {},
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

test('wireBackground registers a chrome.runtime.onMessage listener (slice 5 router wired)', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  globalThis.chrome = globalThis.chrome ?? {};
  globalThis.chrome.storage = globalThis.chrome.storage ?? {};
  globalThis.chrome.runtime = globalThis.chrome.runtime ?? { id: 'ext-123', onMessage: { addListener() {} } };
  let registered = false;
  const prevOnMessage = globalThis.chrome.runtime.onMessage;
  globalThis.chrome.runtime.onMessage = { addListener: () => { registered = true; } };
  try {
    const store = new Map();
    globalThis.chrome.storage.local = {
      async get(keys) { const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]); const o = {}; for (const k of arr) if (store.has(k)) o[k] = store.get(k); return o; },
      async set(o) { for (const [k, v] of Object.entries(o)) store.set(k, v); },
      async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
    };
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {}, onBeforeRequest() {}, onBeforeRedirect() {}, onCommitted() {}, onHistoryStateUpdated() {},
      async updateTabUrl() {}, async getTab() { return null; }, async sendMessage() { return { received: true }; },
      registerAlarm() {},
    };
    const fakeApi = { async getStatus() { return null; }, async claimNextJob() { return null; }, async failJob() {}, async skipJob() {}, async sendHeartbeat() {}, async getDecision() { return null; }, async resolveDecision() {}, async completeJob() {}, async overrideJobUrl() { return null; } };
    mod.wireBackground({ chrome: fakeChrome, api: fakeApi });
    assert.equal(registered, true, 'messageRouter registered on chrome.runtime.onMessage');
  } finally {
    globalThis.chrome.runtime.onMessage = prevOnMessage;
    await cleanup();
  }
});
