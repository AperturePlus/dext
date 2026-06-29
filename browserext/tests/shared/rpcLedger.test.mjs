import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function actionResult(o = {}) {
  return { op: 'ACTION_RESULT', rpcId: 'r1', jobId: 'j1', ok: true, invoked: true, navigationExpected: true, effectApplied: false, ...o };
}

test('markReceived inserts received; idempotent on repeat', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    assert.equal(l.has('r1'), false);
    l.markReceived('r1');
    assert.equal(l.get('r1').stage, 'received');
    l.markReceived('r1');   // repeat → still received, no throw
    assert.equal(l.get('r1').stage, 'received');
  } finally { await cleanup(); }
});

test('markDone stores the cached ActionResult', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    const r = actionResult();
    l.markDone('r1', r);
    assert.equal(l.get('r1').stage, 'done');
    assert.equal(l.get('r1').result, r);
  } finally { await cleanup(); }
});

test('clear removes an entry', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    l.markReceived('r1');
    l.clear('r1');
    assert.equal(l.has('r1'), false);
    assert.equal(l.get('r1'), undefined);
  } finally { await cleanup(); }
});

test('a done entry is still received-safe: get returns done (amend §4.3 repeat re-emits cached)', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/rpcLedger.ts', 'rpcLedger.ts');
  try {
    const l = mod.createRpcLedger();
    const r = actionResult();
    l.markDone('r1', r);
    // a repeat PERFORM_ACTION for r1 must NOT re-execute; the router checks stage==='done' and re-emits
    const entry = l.get('r1');
    assert.equal(entry.stage, 'done');
    assert.equal(entry.result.ok, true);
  } finally { await cleanup(); }
});
