import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

async function importTsModule(sourcePath, outputName) {
  const outDir = await mkdtemp(join(tmpdir(), 'ycl-utils-test-'));
  const result = ts.transpileModule(
    await (await import('node:fs/promises')).readFile(new URL(sourcePath, import.meta.url), 'utf8'),
    {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: outputName,
    },
  );
  const outFile = join(outDir, outputName.replace(/\.ts$/, '.mjs'));
  await (await import('node:fs/promises')).writeFile(outFile, result.outputText, 'utf8');
  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return {
    mod,
    cleanup: () => rm(outDir, { recursive: true, force: true }),
  };
}

function importUtils() {
  return importTsModule('../src/utils.ts', 'utils.ts');
}

function importHostPolicy() {
  return importTsModule('../src/hostPolicy.ts', 'hostPolicy.ts');
}

function importHtmlCleanup() {
  return importTsModule('../src/htmlCleanup.ts', 'htmlCleanup.ts');
}

test('urlMatches rejects explicit ports, including defaults', async () => {
  const { mod, cleanup } = await importUtils();
  try {
    assert.equal(mod.urlMatches('https://x.edu.cn/a?b=1', 'http://x.edu.cn/a?b=1'), true);
    assert.equal(mod.urlMatches('https://x.edu.cn:443/a', 'https://x.edu.cn/a'), false);
    assert.equal(mod.urlMatches('https://x.edu.cn:8080/a', 'https://x.edu.cn/a'), false);
    assert.equal(mod.urlMatches('https://x.edu.cn/a', 'http://x.edu.cn:80/a'), false);
  } finally {
    await cleanup();
  }
});

test('capture cleanup strips common footer containers', async () => {
  const { mod, cleanup } = await importHtmlCleanup();
  try {
    assert.equal(mod.shouldStripElementDescriptor('footer', { tagName: 'footer' }), true);
    assert.equal(mod.shouldStripElementDescriptor('footer', { role: 'contentinfo' }), true);
    assert.equal(mod.shouldStripElementDescriptor('footer', { id: 'siteFooter' }), true);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'copyright page-end' }), true);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'foot-wrap' }), true);
  } finally {
    await cleanup();
  }
});

test('capture cleanup only strips headers when requested', async () => {
  const { mod, cleanup } = await importHtmlCleanup();
  try {
    const footer = fakeElement('div', { class: 'site-footer' });
    const header = fakeElement('div', { id: 'pageHeader' });
    const body = fakeElement('main', { class: 'content' });
    mod.stripCaptureNoise(fakeRoot([footer, header, body]), { stripHeader: false });
    assert.equal(footer.removed, true);
    assert.equal(header.removed, false);
    assert.equal(body.removed, false);

    const detailHeader = fakeElement('div', { id: 'pageHeader' });
    mod.stripCaptureNoise(fakeRoot([detailHeader]), { stripHeader: true });
    assert.equal(detailHeader.removed, true);
  } finally {
    await cleanup();
  }
});

test('capture cleanup avoids weak header and footer false positives', async () => {
  const { mod, cleanup } = await importHtmlCleanup();
  try {
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'bottom' }), false);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'footnote' }), false);
    assert.equal(mod.shouldStripElementDescriptor('header', { className: 'department-head' }), false);
    assert.equal(mod.shouldStripElementDescriptor('header', { className: 'headmaster-card' }), false);
    assert.equal(mod.shouldStripElementDescriptor('header', { id: 'headline' }), false);
  } finally {
    await cleanup();
  }
});

test('capture cleanup does not strip layout wrappers that merely contain a header/footer class', async () => {
  // Regression: NEU CSE detail pages wrap the ENTIRE body (header + article +
  // footer) in <div class="wrapper header">. Stripping it left an empty body,
  // which marked every professor page as terminal_unavailable:empty_page.
  const { mod, cleanup } = await importHtmlCleanup();
  try {
    assert.equal(mod.shouldStripElementDescriptor('header', { className: 'wrapper header' }), false);
    assert.equal(mod.shouldStripElementDescriptor('header', { className: 'wrapper topbar' }), false);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'wrapper footer' }), false);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'content copyright' }), false);

    // A primary header/footer class still strips (single-class or first-segment).
    assert.equal(mod.shouldStripElementDescriptor('header', { className: 'header' }), true);
    assert.equal(mod.shouldStripElementDescriptor('header', { className: 'site-header nav-extra' }), true);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'footer' }), true);
    assert.equal(mod.shouldStripElementDescriptor('footer', { className: 'copyright page-end' }), true);
  } finally {
    await cleanup();
  }
});

test('capture cleanup keeps a <header> that swallows the page body (unclosed-tag regression)', async () => {
  // Regression: LZU 土木工程与力学学院 detail pages emit
  // <header class="header__block"> and never close it, so the parser nests nav +
  // #services-section + footer inside it. Stripping it deleted the entire body
  // and every professor detail page was skipped as terminal_unavailable:empty_page
  // (professors table got 0 rows for that college). The swallow-guard must leave
  // such a header in place while still stripping an ordinary small header.
  const { mod, cleanup } = await importHtmlCleanup();
  try {
    // 10 content elements live inside the header, 0 outside -> header swallows the page.
    const inside = Array.from({ length: 10 }, () => fakeElement('div'));
    const swallowingHeader = fakeElement('header', { class: 'header__block' }, inside);
    mod.stripCaptureNoise(fakeRoot([swallowingHeader, ...inside]), { stripHeader: true });
    assert.equal(swallowingHeader.removed, false);

    // An ordinary small header (few descendants vs. a large page) is still stripped.
    const smallHeader = fakeElement('header', { class: 'header__block' });
    const filler = Array.from({ length: 20 }, () => fakeElement('div'));
    mod.stripCaptureNoise(fakeRoot([smallHeader, ...filler]), { stripHeader: true });
    assert.equal(smallHeader.removed, true);
  } finally {
    await cleanup();
  }
});

function fakeElement(tagName, attrs = {}, descendants = []) {
  return {
    tagName,
    removed: false,
    getAttribute(name) {
      return attrs[name] ?? null;
    },
    remove() {
      this.removed = true;
    },
    querySelectorAll(selector) {
      if (selector === '*') return descendants;
      return [];
    },
  };
}

function fakeRoot(elements) {
  return {
    querySelectorAll(selector) {
      if (selector === '*') return elements;
      return [];
    },
  };
}

test('same-site helpers reject explicit-port redirects', async () => {
  const { mod, cleanup } = await importUtils();
  try {
    assert.equal(mod.sameHost('https://www.x.edu.cn/a', 'http://x.edu.cn/b'), true);
    assert.equal(mod.sameHost('https://x.edu.cn:443/a', 'https://x.edu.cn/b'), false);
    assert.equal(mod.sameSite('https://math.lzu.edu.cn/a', 'https://ce.lzu.edu.cn/b'), true);
    assert.equal(mod.sameSite('https://ce.lzu.edu.cn:8080/a', 'https://math.lzu.edu.cn/b'), false);
  } finally {
    await cleanup();
  }
});

test('isWechatHost detects weixin domains (case-insensitive)', async () => {
  const { mod, cleanup } = await importHostPolicy();
  try {
    assert.equal(mod.isWechatHost('mp.weixin.qq.com'), true);
    assert.equal(mod.isWechatHost('weixin.qq.com'), true);
    assert.equal(mod.isWechatHost('MP.WEIXIN.QQ.COM'), true);
    assert.equal(mod.isWechatHost('www.edu.cn'), false);
    assert.equal(mod.isWechatHost(''), false);
  } finally {
    await cleanup();
  }
});

test('isWechatUrl inspects hostname and rejects malformed input', async () => {
  const { mod, cleanup } = await importHostPolicy();
  try {
    assert.equal(mod.isWechatUrl('https://mp.weixin.qq.com/s?__biz=abc'), true);
    assert.equal(mod.isWechatUrl('http://weixin.qq.com/x'), true);
    assert.equal(mod.isWechatUrl('https://www.edu.cn/news/1'), false);
    assert.equal(mod.isWechatUrl('not a url'), false);
  } finally {
    await cleanup();
  }
});

test('wechat hosts are not part of BLOCKED_HOSTS', async () => {
  const { mod, cleanup } = await importHostPolicy();
  try {
    assert.equal(mod.isAssistantBlockedHost('mp.weixin.qq.com'), false);
    assert.equal(mod.isAssistantBlockedHost('weixin.qq.com'), false);
    assert.equal(mod.isAssistantBlockedHost('dx.scu.edu.cn'), true);
  } finally {
    await cleanup();
  }
});

test('isAllowedFetchHost mirrors backend allowlist (no ac.cn, dot-boundary)', async () => {
  const { mod, cleanup } = await importHostPolicy();
  try {
    assert.equal(mod.isAllowedFetchHost('x.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('www.x.edu.cn'), true);
    assert.equal(mod.isAllowedFetchHost('foo.github.io'), true);
    assert.equal(mod.isAllowedFetchHost('github.io'), true);
    // ac.cn deliberately excluded (mirrors backend).
    assert.equal(mod.isAllowedFetchHost('www.ac.cn'), false);
    assert.equal(mod.isAllowedFetchHost('ac.cn'), false);
    // dot-boundary: suffix-only match must not pass.
    assert.equal(mod.isAllowedFetchHost('notedu.cn'), false);
    assert.equal(mod.isAllowedFetchHost('evilgithub.io'), false);
    // offsite / wechat hosts not allowed.
    assert.equal(mod.isAllowedFetchHost('example.com'), false);
    assert.equal(mod.isAllowedFetchHost('mp.weixin.qq.com'), false);
    assert.equal(mod.isAllowedFetchHost(''), false);
  } finally {
    await cleanup();
  }
});

test('isDisallowedRedirectHost flags wechat and non-allowlisted hosts', async () => {
  const { mod, cleanup } = await importHostPolicy();
  try {
    assert.equal(mod.isDisallowedRedirectHost('mp.weixin.qq.com'), true);
    assert.equal(mod.isDisallowedRedirectHost('example.com'), true);
    assert.equal(mod.isDisallowedRedirectHost('www.ac.cn'), true);
    assert.equal(mod.isDisallowedRedirectHost('x.edu.cn'), false);
    assert.equal(mod.isDisallowedRedirectHost('foo.github.io'), false);
  } finally {
    await cleanup();
  }
});
