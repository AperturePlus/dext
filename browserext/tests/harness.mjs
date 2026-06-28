import { mkdtemp, rm, readdir, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import ts from 'typescript';

export async function importTsModule(sourcePath, outputName) {
  const sourceFile = new URL(sourcePath, import.meta.url);
  const sourceDir = new URL('.', sourceFile);
  const outDir = await mkdtemp(join(tmpdir(), 'dext-ext-test-'));

  // Copy + transpile every .ts sibling so relative imports in the target module resolve.
  for (const name of await readdir(sourceDir)) {
    if (!name.endsWith('.ts')) continue;
    const result = ts.transpileModule(
      await readFile(new URL(name, sourceDir), 'utf8'),
      {
        compilerOptions: {
          module: ts.ModuleKind.ESNext,
          target: ts.ScriptTarget.ES2022,
        },
        fileName: name,
      },
    );
    const outName = name.replace(/\.ts$/, '.mjs');
    const outFile = join(outDir, outName);
    // Rewrite extension-less *and* ".js"-suffixed relative imports to ".mjs" so
    // Node ESM resolves them. The ".js" form is what src/*.ts uses (correct for the
    // browser ESM loader, which needs explicit extensions); strip it before adding ".mjs".
    const code = result.outputText.replace(
      /from\s+(['"])(\.[^'"]+)\1/g,
      (m, q, spec) => {
        const base = spec.replace(/\.(mjs|js)$/, '');
        return `from ${q}${base}.mjs${q}`;
      },
    );
    await writeFile(outFile, code, 'utf8');
  }

  const requestedOutName = outputName.replace(/\.ts$/, '.mjs');
  const outFile = join(outDir, requestedOutName);
  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return {
    mod,
    cleanup: () => rm(outDir, { recursive: true, force: true }),
  };
}
