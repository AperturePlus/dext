import { build } from 'esbuild';

// Slice 6: official build is now gate-ON (spec §6.1/§6.2). DEXTC_EXCLUSIVE_CONTROL=0
// opts back out to a no-op build (e.g. to run alongside an active userscript).
const GATE = process.env.DEXTC_EXCLUSIVE_CONTROL === '0' ? 'false' : 'true';

const shared = {
  bundle: true,
  target: 'es2022',
  platform: 'browser',
  sourcemap: false,
  minify: false,
  logLevel: 'info',
  define: {
    'EXCLUSIVE_CONTROL_ENABLED': GATE,
  },
};

await Promise.all([
  build({
    ...shared,
    entryPoints: ['src/background.ts'],
    outfile: 'dist/background.js',
    format: 'esm',
  }),
  build({
    ...shared,
    entryPoints: ['src/content/index.ts'],
    outfile: 'dist/content.js',
    format: 'iife',
    loader: { '.css': 'text' },
  }),
]);
