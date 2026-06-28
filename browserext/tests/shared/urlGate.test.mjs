import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('urlMatches: ignores protocol + trailing slash; sorts query; ports reject', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.urlMatches('http://x.edu.cn/p', 'https://x.edu.cn/p'), true);
    assert.equal(mod.urlMatches('http://x.edu.cn/p/', 'https://x.edu.cn/p'), true);
    assert.equal(mod.urlMatches('http://x.edu.cn/p?a=1&b=2', 'https://x.edu.cn/p?b=2&a=1'), true);
    assert.equal(mod.urlMatches('http://x.edu.cn/p?a=1', 'https://x.edu.cn/p?a=2'), false);
    assert.equal(mod.urlMatches('http://x.edu.cn/p', 'https://y.edu.cn/p'), false);
    assert.equal(mod.urlMatches('http://x.edu.cn/p', 'https://x.edu.cn/q'), false);
    assert.equal(mod.urlMatches('http://x.edu.cn:8080/p', 'http://x.edu.cn/p'), false);
    assert.equal(mod.urlMatches('not a url', 'http://x.edu.cn/p'), false);
  } finally {
    await cleanup();
  }
});

test('sameCrawlSite: edu.cn shares site root; github.io must match exactly', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.sameCrawlSite('https://a.xjtu.edu.cn/p', 'https://xjtu.edu.cn/'), true);
    assert.equal(mod.sameCrawlSite('https://xjtu.edu.cn/p', 'https://xjtu.edu.cn/q'), true);
    assert.equal(mod.sameCrawlSite('https://xjtu.edu.cn/', 'https://attacker.edu.cn/'), false);
    assert.equal(mod.sameCrawlSite('https://foo.github.io/', 'https://foo.github.io/x'), true);
    assert.equal(mod.sameCrawlSite('https://foo.github.io/', 'https://bar.github.io/'), false);
    assert.equal(mod.sameCrawlSite('http://xjtu.edu.cn/', 'https://xjtu.edu.cn/'), true);
  } finally {
    await cleanup();
  }
});

test('redirectKind: wechat vs offsite vs onsite', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.redirectKind('https://mp.weixin.qq.com/s?x=1', 'https://xjtu.edu.cn/'), 'wechat');
    assert.equal(mod.redirectKind('https://weixin.qq.com/', 'https://xjtu.edu.cn/'), 'wechat');
    assert.equal(mod.redirectKind('https://attacker.edu.cn/', 'https://xjtu.edu.cn/'), 'offsite');
    assert.equal(mod.redirectKind('https://a.xjtu.edu.cn/x', 'https://xjtu.edu.cn/'), 'onsite');
    assert.equal(mod.redirectKind('https://xjtu.edu.cn/renxueguang', 'https://xjtu.edu.cn/'), 'onsite');
  } finally {
    await cleanup();
  }
});

test('hasExplicitPort + siteRoot mirror userscript utils', async () => {
  const { mod, cleanup } = await importTsModule('../src/shared/urlGate.ts', 'urlGate.ts');
  try {
    assert.equal(mod.hasExplicitPort('http://x.edu.cn:8080/'), true);
    assert.equal(mod.hasExplicitPort('http://x.edu.cn/'), false);
    assert.equal(mod.siteRoot('a.xjtu.edu.cn'), 'xjtu.edu.cn');
    assert.equal(mod.siteRoot('foo.github.io'), 'github.io');
    assert.equal(mod.siteRoot('x.scu.com.cn'), 'scu.com.cn');
  } finally {
    await cleanup();
  }
});
