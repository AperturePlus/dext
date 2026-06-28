import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// harness injects EXCLUSIVE_CONTROL_ENABLED = true.

function fakeDoc(hostname) {
  const attrs = new Map();
  const el = {
    getAttribute: (n) => (attrs.has(n) ? attrs.get(n) : null),
    setAttribute: (n, v) => attrs.set(n, v),
    removeAttribute: (n) => attrs.delete(n),
    _has: (n) => attrs.has(n),
    _get: (n) => attrs.get(n),
  };
  return { el, hostname };
}

test('gated ON: sets data-dext-extension-controller=v1 on allowed host', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('xjtu.edu.cn');
    await mod.bootstrapContent({ hostname: doc.hostname, documentElement: doc.el, now: 1000 });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
  } finally { await cleanup(); }
});

test('gated ON: sets marker even on disallowed host, then early-returns (no onAllowedHost)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('mp.weixin.qq.com');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    // marker is set on EVERY page (spec §5.2), but disallowed host must not proceed
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, false, 'disallowed host must early-return before onAllowedHost');
  } finally { await cleanup(); }
});

test('gated ON: allowed host proceeds to onAllowedHost hook (slice 1 stops there)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('foo.github.io');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, true);
  } finally { await cleanup(); }
});
