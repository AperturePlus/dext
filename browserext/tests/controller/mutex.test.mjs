import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('mutex serializes: second acquire waits for first release', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/mutex.ts', 'mutex.ts');
  try {
    const m = mod.createMutex();
    const order = [];
    const release1 = await m.acquire();
    let p2Resolved = false;
    const p2 = m.acquire().then((r) => { p2Resolved = true; order.push('second'); return r; });
    // give the microtask queue a chance — p2 must NOT have acquired yet
    await Promise.resolve();
    assert.equal(p2Resolved, false, 'second acquire must wait');
    order.push('first-release');
    release1();
    const release2 = await p2;
    release2();
    assert.deepEqual(order, ['first-release', 'second']);
  } finally { await cleanup(); }
});

test('mutex releases on thrown error so next acquire is not deadlocked', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/mutex.ts', 'mutex.ts');
  try {
    const m = mod.createMutex();
    const release = await m.acquire();
    // simulate a critical section that throws; release() in finally must still run
    let threw = false;
    try {
      try {
        throw new Error('boom');
      } finally {
        release();
      }
    } catch {
      threw = true;
    }
    assert.equal(threw, true, 'error propagated as expected');
    // the real contract: the lock is released, so the next acquire must not hang
    const release2 = await m.acquire();
    release2();
  } finally {
    await cleanup();
  }
});
