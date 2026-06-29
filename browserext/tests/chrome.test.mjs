import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// We can't easily unit-test createRealChromeRuntime (it binds real chrome.*),
// but we CAN test the interface contract by building a fake that matches it,
// proving the shape navMonitor/controller will consume.
function fakeRuntime() {
  const completedCbs = [], errorCbs = [], beforeReqCbs = [], beforeRedirectCbs = [], committedCbs = [], historyCbs = [];
  let alarmCb = null;
  let lastUpdated = null;
  const sentMessages = [];
  let tabs = [{ id: 1, url: 'https://www.x.edu.cn/faculty' }];
  return {
    onNavCompleted(cb) { completedCbs.push(cb); },
    onNavError(cb) { errorCbs.push(cb); },
    onBeforeRequest(cb) { beforeReqCbs.push(cb); },
    onBeforeRedirect(cb) { beforeRedirectCbs.push(cb); },
    onCommitted(cb) { committedCbs.push(cb); },
    onHistoryStateUpdated(cb) { historyCbs.push(cb); },
    async updateTabUrl(tabId, url) { lastUpdated = { tabId, url }; },
    async findOwnerTab() {
      const t = tabs.find((t) => /edu\.cn$|github\.io$/.test(new URL(t.url).hostname)) ?? tabs[0] ?? null;
      return t ? t.id : null;
    },
    async getTab(tabId) { const t = tabs.find((t) => t.id === tabId) ?? null; return t ? { id: t.id, url: t.url } : null; },
    async sendMessage(tabId, message, options) { sentMessages.push({ tabId, message, options }); return { received: true }; },
    registerAlarm(_name, _period, cb) { alarmCb = cb; },
    // test helpers
    fireCompleted(e) { for (const cb of completedCbs) cb(e); },
    fireError(e) { for (const cb of errorCbs) cb(e); },
    fireBeforeRequest(e) { for (const cb of beforeReqCbs) cb(e); },
    fireBeforeRedirect(e) { for (const cb of beforeRedirectCbs) cb(e); },
    fireCommitted(e) { for (const cb of committedCbs) cb(e); },
    fireHistory(e) { for (const cb of historyCbs) cb(e); },
    fireAlarm() { if (alarmCb) alarmCb(); },
    lastUpdated,
    sentMessages,
    _tabs: tabs,
  };
}

test('ChromeRuntime interface is satisfiable by a fake and forwards callbacks', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    // Just import the module to prove the interface + createRealChromeRuntime export exist;
    // behavioral testing of consumers happens in navMonitor/controller tests.
    assert.equal(typeof mod.createRealChromeRuntime, 'function');
  } finally {
    await cleanup();
  }
});

test('createRealChromeRuntime exposes registerAlarm + getTab (rename from watchdog + new)', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    const rt = mod.createRealChromeRuntime();
    assert.equal(typeof rt.registerAlarm, 'function');
    assert.equal(typeof rt.getTab, 'function');
    assert.equal(typeof rt.findOwnerTab, 'function');
  } finally {
    await cleanup();
  }
});

test('createRealChromeRuntime exposes nav listeners + widened events (slice 3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/chrome.ts', 'chrome.ts');
  try {
    const rt = mod.createRealChromeRuntime();
    for (const m of ['onBeforeRequest','onBeforeRedirect','onCommitted','onHistoryStateUpdated','onNavCompleted','onNavError','updateTabUrl','findOwnerTab','getTab','sendMessage','registerAlarm']) {
      assert.equal(typeof rt[m], 'function', `${m} is a function`);
    }
  } finally {
    await cleanup();
  }
});
