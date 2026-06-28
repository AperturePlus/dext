import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

/** Bundle a TS module (and all relative imports recursively) into a temp .mjs
 *  that Node ESM can load. Replaces the per-file TS-API transpile, which could
 *  not follow imports into src/content/ or src/controller/ subdirectories.
 *  Injects EXCLUSIVE_CONTROL_ENABLED = true so gated-ON paths are exercised. */
export async function importTsModule(sourcePath, outputName) {
  const sourceAbs = fileURLToPath(new URL(sourcePath, import.meta.url));
  const outDir = await mkdtemp(join(tmpdir(), 'dext-ext-test-'));
  const requestedOutName = outputName.replace(/\.ts$/, '.mjs');
  const outFile = join(outDir, requestedOutName);

  await build({
    entryPoints: [sourceAbs],
    bundle: true,
    format: 'esm',
    target: 'es2022',
    platform: 'neutral',
    outfile: outFile,
    sourcemap: false,
    write: true,
    logLevel: 'silent',
    resolveExtensions: ['.ts', '.js', '.mjs'],
    define: { 'EXCLUSIVE_CONTROL_ENABLED': 'true' },
  });

  const mod = await import(`file:///${outFile.replaceAll('\\', '/')}`);
  return { mod, cleanup: () => rm(outDir, { recursive: true, force: true }) };
}
