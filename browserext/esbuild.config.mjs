import { build } from 'esbuild';

const GATE = process.env.DEXTC_EXCLUSIVE_CONTROL === '1' ? 'true' : 'false';

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
