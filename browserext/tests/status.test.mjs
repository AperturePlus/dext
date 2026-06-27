import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

test('classifyNavigation maps status codes', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    // 2xx → ok
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 200 }), 'ok');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 204 }), 'ok');
    // 3xx is not delivered by onCompleted (final 2xx is), but if seen → ok
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 301 }), 'ok');
    // 404/410 → not_found
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 404 }), 'not_found');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 410 }), 'not_found');
    // 429 → rate_limited (no action; the backend's 429 path handles backoff)
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 429 }), 'rate_limited');
    // 5xx gateway
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 502 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 503 }), 'gateway');
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 504 }), 'gateway');
    // 500 → ok (not a gateway transient per backend spec)
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 500 }), 'ok');
    // other 4xx (403/401) → ok (let the userscript capture; backend's unavailable-page
    // assessment + LLM handle semantic exclusion, not this probe)
    assert.equal(mod.classifyNavigation({ kind: 'completed', statusCode: 403 }), 'ok');
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

test('GATEWAY_STATUSES and DEAD_STATUSES are exported', async () => {
  const { mod, cleanup } = await importTsModule('../src/status.ts', 'status.ts');
  try {
    assert.ok(mod.GATEWAY_STATUSES.has(502));
    assert.ok(mod.GATEWAY_STATUSES.has(503));
    assert.ok(mod.GATEWAY_STATUSES.has(504));
    assert.ok(mod.DEAD_STATUSES.has(404));
    assert.ok(mod.DEAD_STATUSES.has(410));
  } finally {
    await cleanup();
  }
});
