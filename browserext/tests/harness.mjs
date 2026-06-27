import { mkdtemp, rm, readFile, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import ts from 'typescript';

export async function importTsModule(sourcePath, outputName) {
  const outDir = await mkdtemp(join(tmpdir(), 'dext-ext-test-'));
  const result = ts.transpileModule(
    await readFile(new URL(sourcePath, import.meta.url), 'utf8'),
    {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
      fileName: outputName,
    },
  );
  const outFile = join(outDir, outputName.replace(/\.ts$/, '.mjs'));
  await writeFile(outFile, result.outputText, 'utf8');
  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return {
    mod,
    cleanup: () => rm(outDir, { recursive: true, force: true }),
  };
}
