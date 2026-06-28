import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('computeBackoffMs: exponential 2s base, 60s cap, 0 for non-positive', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/backoff.ts', 'backoff.ts');
  try {
    assert.equal(mod.computeBackoffMs(0), 0);
    assert.equal(mod.computeBackoffMs(-1), 0);
    assert.equal(mod.computeBackoffMs(1), 2000);
    assert.equal(mod.computeBackoffMs(2), 4000);
    assert.equal(mod.computeBackoffMs(3), 8000);
    assert.equal(mod.computeBackoffMs(4), 16000);
    assert.equal(mod.computeBackoffMs(5), 32000);
    assert.equal(mod.computeBackoffMs(6), 60000);   // 64s capped to 60s
    assert.equal(mod.computeBackoffMs(7), 60000);   // stays capped
    assert.equal(mod.computeBackoffMs(100), 60000);
  } finally {
    await cleanup();
  }
});

test('nextRetryAt = now + computeBackoffMs(count)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/backoff.ts', 'backoff.ts');
  try {
    assert.equal(mod.nextRetryAt(0, 1000), 1000);          // no backoff → retry immediately (now)
    assert.equal(mod.nextRetryAt(1, 1000), 3000);          // 1000 + 2000
    assert.equal(mod.nextRetryAt(3, 1000), 9000);          // 1000 + 8000
    assert.equal(mod.nextRetryAt(6, 1000), 61000);         // 1000 + 60000 (capped)
  } finally {
    await cleanup();
  }
});
