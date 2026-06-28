import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// amend §8.1 #16: 200/304 → ok; 500 → gateway; 401/403/204/205 → unexpected_status;
// 301/302 (defensive — never seen in production because onBeforeRedirect is raw)
// → unexpected_status.

test('classifyNavigation maps status codes per amend §3.1', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 200 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 201 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 304 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 404 }), 'not_found');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 410 }), 'not_found');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 429 }), 'rate_limited');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 500 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 502 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 503 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 504 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 204 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 205 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 401 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 403 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 301 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 302 }), 'unexpected_status');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 400 }), 'unexpected_status');
  } finally {
    await cleanup();
  }
});

test('classifyNavigation maps network errors to nav_error', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.equal(mod.classifyNavigation({ kind: 'error', error: 'ERR_CONNECTION_REFUSED' }), 'nav_error');
    assert.equal(mod.classifyNavigation({ kind: 'error', error: 'ERR_NAME_NOT_RESOLVED' }), 'nav_error');
    assert.equal(mod.classifyNavigation({ kind: 'error', error: 'ERR_TIMED_OUT' }), 'nav_error');
  } finally {
    await cleanup();
  }
});

test('DEAD/RATE_LIMITED/GATEWAY sets exported; GATEWAY now includes 500', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.ok(mod.DEAD_STATUSES.has(404));
    assert.ok(mod.DEAD_STATUSES.has(410));
    assert.equal(mod.RATE_LIMITED_STATUS, 429);
    assert.ok(mod.GATEWAY_STATUSES.has(500));
    assert.ok(mod.GATEWAY_STATUSES.has(502));
    assert.ok(mod.GATEWAY_STATUSES.has(503));
    assert.ok(mod.GATEWAY_STATUSES.has(504));
  } finally {
    await cleanup();
  }
});
