import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('not_found → terminal skip, no budget (any attempt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    for (const attempt of [1, 2, 3, 5]) {
      const d = mod.funnelDecision('not_found', attempt);
      assert.equal(d.kind, 'skip', `attempt ${attempt}`);
      assert.equal(d.reason, 'not_found');
    }
  } finally { await cleanup(); }
});

test('rate_limited → immediate fail rate_limited, no refresh (any attempt)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    for (const attempt of [1, 2, 3]) {
      const d = mod.funnelDecision('rate_limited', attempt);
      assert.equal(d.kind, 'fail', `attempt ${attempt}`);
      assert.deepEqual(d.error, { kind: 'rate_limited' });
    }
  } finally { await cleanup(); }
});

test('gateway: attempts 1,2 retry; attempt 3 fails gateway_5xx', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    assert.equal(mod.funnelDecision('gateway', 1).kind, 'retry_navigate');
    assert.equal(mod.funnelDecision('gateway', 2).kind, 'retry_navigate');
    const d = mod.funnelDecision('gateway', 3);
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'gateway_5xx' });
  } finally { await cleanup(); }
});

test('unexpected_status: attempts 1,2 retry; attempt 3 fails unexpected_status:<code>', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    assert.equal(mod.funnelDecision('unexpected_status', 1, '403').kind, 'retry_navigate');
    assert.equal(mod.funnelDecision('unexpected_status', 2, '403').kind, 'retry_navigate');
    const d = mod.funnelDecision('unexpected_status', 3, '403');
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'unexpected_status', statusCode: 403 });
  } finally { await cleanup(); }
});

test('nav_error: attempts 1,2 retry; attempt 3 fails nav_error:<error>', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    assert.equal(mod.funnelDecision('nav_error', 1, 'ERR_CONNECTION_REFUSED').kind, 'retry_navigate');
    assert.equal(mod.funnelDecision('nav_error', 2, 'ERR_CONNECTION_REFUSED').kind, 'retry_navigate');
    const d = mod.funnelDecision('nav_error', 3, 'ERR_CONNECTION_REFUSED');
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'nav_error', error: 'ERR_CONNECTION_REFUSED' });
  } finally { await cleanup(); }
});

test('unexpected_status without detail → fail with statusCode 0 (defensive)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/funnel.ts', 'funnel.ts');
  try {
    const d = mod.funnelDecision('unexpected_status', 3);
    assert.equal(d.kind, 'fail');
    assert.deepEqual(d.error, { kind: 'unexpected_status', statusCode: 0 });
  } finally { await cleanup(); }
});
