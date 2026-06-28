import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('initialControllerState is unbound idle with null navigation/pendingRpc/lastError', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/state.ts', 'state.ts');
  try {
    const s = mod.initialControllerState(1000);
    assert.equal(s.boundTabId, null);
    assert.equal(s.phase, 'idle');
    assert.equal(s.navigation, null);
    assert.equal(s.pendingRpc, null);
    assert.equal(s.lastError, null);
    assert.equal(s.connected, false);
  } finally { await cleanup(); }
});
