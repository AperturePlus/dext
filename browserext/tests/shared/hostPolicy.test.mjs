import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('isAllowedFetchHost accepts edu.cn + github.io hosts (dot-boundary)', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/hostPolicy.ts', 'hostPolicy.ts');
  try {
    assert.equal(mod.isAllowedFetchHost('xjtu.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('a.b.xjtu.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('foo.github.io'), true);
    assert.equal(mod.isAllowedFetchHost('github.io'), true);
  } finally { await cleanup(); }
});

test('isAllowedFetchHost rejects non-suffix + lookalikes, accepts any edu.cn/github.io', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/hostPolicy.ts', 'hostPolicy.ts');
  try {
    // isAllowedFetchHost accepts ANY *.edu.cn / *.github.io host — it's the
    // "is this a host we'd ever run on" gate, NOT the same-crawl-site landing
    // gate (spec §3.2 condition 3: xjtu.edu.cn → attacker.edu.cn is rejected
    // by the same-site gate, which is slice 3). So attacker.edu.cn is allowed.
    assert.equal(mod.isAllowedFetchHost('attacker.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('notedu.cn'), false);         // dot-boundary
    assert.equal(mod.isAllowedFetchHost('evilgithub.io'), false);    // dot-boundary
    assert.equal(mod.isAllowedFetchHost('mp.weixin.qq.com'), false);
    assert.equal(mod.isAllowedFetchHost(''), false);
    assert.equal(mod.isAllowedFetchHost('example.com'), false);
  } finally { await cleanup(); }
});
