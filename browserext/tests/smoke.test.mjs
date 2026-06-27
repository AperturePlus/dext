import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('harness transpiles and imports a TS module', async () => {
  // Re-import background.ts (the placeholder from Task 1) just to prove the harness works.
  const { mod, cleanup } = await importTsModule('../src/background.ts', 'background.ts');
  try {
    assert.equal(typeof mod, 'object');
  } finally {
    await cleanup();
  }
});
