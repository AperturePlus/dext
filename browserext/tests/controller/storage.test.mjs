import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function fakeArea() {
  const store = new Map();
  return {
    async get(keys) {
      if (keys === null) {
        const obj = {}; for (const [k, v] of store) obj[k] = v; return obj;
      }
      const arr = Array.isArray(keys) ? keys : [keys];
      const obj = {}; for (const k of arr) if (store.has(k)) obj[k] = store.get(k);
      return obj;
    },
    async set(obj) { for (const [k, v] of Object.entries(obj)) store.set(k, v); },
    async remove(keys) {
      const arr = Array.isArray(keys) ? keys : [keys];
      for (const k of arr) store.delete(k);
    },
    _dump() { return Object.fromEntries(store); },
  };
}

test('load returns initial state when storage empty', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const cs = mod.createControllerStorage(fakeArea());
    const s = await cs.load();
    assert.equal(s.boundTabId, null);
    assert.equal(s.phase, 'idle');
    assert.equal(s.navigation, null);
  } finally { await cleanup(); }
});

test('save then load round-trips a bound state', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const area = fakeArea();
    const cs = mod.createControllerStorage(area);
    const s = await cs.load();
    s.boundTabId = 7;
    s.boundAt = 1234;
    s.phase = 'assigned';
    await cs.save(s);
    const reloaded = await cs.load();
    assert.equal(reloaded.boundTabId, 7);
    assert.equal(reloaded.boundAt, 1234);
    assert.equal(reloaded.phase, 'assigned');
  } finally { await cleanup(); }
});

test('saveIfChanged writes only when state differs', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const area = fakeArea();
    const cs = mod.createControllerStorage(area);
    const s = await cs.load();
    s.boundTabId = 5;
    let wrote = await cs.saveIfChanged(await cs.load(), s);
    assert.equal(wrote, true, 'first save writes');
    wrote = await cs.saveIfChanged(s, { ...s });   // JSON-equal
    assert.equal(wrote, false, 'identical state does not write');
    const s2 = { ...s, paused: true };
    wrote = await cs.saveIfChanged(s, s2);
    assert.equal(wrote, true, 'changed state writes');
  } finally { await cleanup(); }
});

test('clear removes the controller key', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/storage.ts', 'storage.ts');
  try {
    const area = fakeArea();
    const cs = mod.createControllerStorage(area);
    const s = await cs.load();
    s.boundTabId = 9;
    await cs.save(s);
    await cs.clear();
    const reloaded = await cs.load();
    assert.equal(reloaded.boundTabId, null);
  } finally { await cleanup(); }
});
