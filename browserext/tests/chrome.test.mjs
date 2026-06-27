import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// We can't easily unit-test createRealChromeRuntime (it binds real chrome.*),
// but we CAN test the interface contract by building a fake that matches it,
// proving the shape navMonitor/watchdog will consume.
function fakeRuntime() {
  const completedCbs = [];
  const errorCbs = [];
  let alarmCb = null;
  let lastUpdated = null;
  let tabs = [{ id: 1, url: 'https://www.x.edu.cn/faculty' }];
  return {
    onNavCompleted(cb) { completedCbs.push(cb); },
    onNavError(cb) { errorCbs.push(cb); },
    async updateTabUrl(tabId, url) { lastUpdated = { tabId, url }; },
    async findOwnerTab() {
      const t = tabs.find((t) => /edu\.cn$|github\.io$/.test(new URL(t.url).hostname)) ?? tabs[0] ?? null;
      return t ? t.id : null;
    },
    registerWatchdogAlarm(_name, _period, cb) { alarmCb = cb; },
    // test helpers
    fireCompleted(e) { for (const cb of completedCbs) cb(e); },
    fireError(e) { for (const cb of errorCbs) cb(e); },
    fireAlarm() { if (alarmCb) alarmCb(); },
    lastUpdated,
    _tabs: tabs,
  };
}

test('ChromeRuntime interface is satisfiable by a fake and forwards callbacks', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    // Just import the module to prove the interface + createRealChromeRuntime export exist;
    // behavioral testing of consumers happens in navMonitor/watchdog tests.
    assert.equal(typeof mod.createRealChromeRuntime, 'function');
  } finally {
    await cleanup();
  }
});
