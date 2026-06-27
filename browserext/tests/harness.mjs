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
    // Add .mjs to extension-less relative imports so Node ESM resolves them.
    const code = result.outputText.replace(
      /from\s+(['"])(\.[^'"]+)\1/g,
      (m, q, spec) => (spec.endsWith('.mjs') ? m : `from ${q}${spec}.mjs${q}`),
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
