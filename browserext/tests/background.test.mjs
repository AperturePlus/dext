import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('wireBackground constructs navMonitor + watchdog and starts both', async () => {
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  try {
    let started = { nav: false, wd: false };
    const fakeChrome = {
      onNavCompleted() {}, onNavError() {},
      async updateTabUrl() {}, async findOwnerTab() { return null; },
      registerWatchdogAlarm() {},
    };
    const fakeApi = { async getStatus() { return null; }, async failJob() {}, async skipJob() {} };
    const fakeStorage = {
      async bumpCount() { return 0; }, async getCount() { return 0; },
      async markVerdictSent() {}, async wasVerdictSent() { return false; },
      async recordRedirect() {}, async shouldRedirect() { return true; }, async clear() {},
    };
    const { navMonitor, watchdog } = mod.wireBackground({ chrome: fakeChrome, api: fakeApi, storage: fakeStorage });
    // monkeypatch start to detect invocation
    const origNavStart = navMonitor.start.bind(navMonitor);
    const origWdStart = watchdog.start.bind(watchdog);
    navMonitor.start = () => { started.nav = true; origNavStart(); };
    watchdog.start = () => { started.wd = true; origWdStart(); };
    navMonitor.start();
    watchdog.start();
    assert.equal(started.nav, true);
    assert.equal(started.wd, true);
  } finally {
    await cleanup();
  }
});
