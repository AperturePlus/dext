import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('isMainFrame: true for frameId 0, false otherwise', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/navEvents.ts', 'navEvents.ts');
  try {
    assert.equal(mod.isMainFrame({ frameId: 0 }), true);
    assert.equal(mod.isMainFrame({ frameId: 1 }), false);
    assert.equal(mod.isMainFrame({ frameId: 2 }), false);
  } finally {
    await cleanup();
  }
});

test('navEvents module loads cleanly (type-only exports; isMainFrame is the value export)', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/navEvents.ts', 'navEvents.ts');
  try {
    assert.equal(typeof mod.isMainFrame, 'function');
  } finally {
    await cleanup();
  }
});
