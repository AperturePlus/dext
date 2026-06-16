import assert from 'node:assert/strict';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import ts from 'typescript';

async function importUtils() {
  const outDir = await mkdtemp(join(tmpdir(), 'ycl-utils-test-'));
  const result = ts.transpileModule(
    await (await import('node:fs/promises')).readFile(new URL('../src/utils.ts', import.meta.url), 'utf8'),
    {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: 'utils.ts',
    },
  );
  const outFile = join(outDir, 'utils.mjs');
  await (await import('node:fs/promises')).writeFile(outFile, result.outputText, 'utf8');
  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return {
    mod,
    cleanup: () => rm(outDir, { recursive: true, force: true }),
  };
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
