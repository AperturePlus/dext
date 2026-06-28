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
