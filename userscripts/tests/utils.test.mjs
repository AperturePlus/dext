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

function fakeElement(tagName, attrs = {}) {
  return {
    tagName,
    removed: false,
    getAttribute(name) {
      return attrs[name] ?? null;
    },
    remove() {
      this.removed = true;
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
