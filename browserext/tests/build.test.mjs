import assert from 'node:assert/strict';
import test from 'node:test';
import { existsSync, rmSync, readFileSync } from 'node:fs';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const distBg = join(root, 'dist', 'background.js');
const distContent = join(root, 'dist', 'content.js');

function build() {
  execFileSync(process.execPath, ['esbuild.config.mjs'], { cwd: root, stdio: 'pipe' });
}

test('build emits dist/background.js and dist/content.js', () => {
  rmSync(join(root, 'dist'), { recursive: true, force: true });
  try {
    build();
    assert.ok(existsSync(distBg), 'dist/background.js missing');
    assert.ok(existsSync(distContent), 'dist/content.js missing');
  } finally {
    // leave dist for inspection; gitignored anyway
  }
});

test('default build has EXCLUSIVE_CONTROL_ENABLED = true (gate on, slice 6)', () => {
  rmSync(join(root, 'dist'), { recursive: true, force: true });
  try {
    build();
    const bg = readFileSync(distBg, 'utf8');
    // Controller guard `if (!GATE) return` with GATE=true folds to `if (false) return` —
    // the early-return is NOT taken, so the body runs (spec §6.1 gate ON).
    assert.match(bg, /if \(false\) return/, 'controller body runs when gate on');
    assert.doesNotMatch(bg, /if \(true\) return/, 'no gate-off early-return should remain in default build');
    const content = readFileSync(distContent, 'utf8');
    // Content guard `if (GATE) {...}` with GATE=true folds to `if (true) {...}` —
    // the body runs (marker set, panel mounted, RPC wired).
    assert.match(content, /if \(true\) \{/, 'content body runs when gate on');
    assert.doesNotMatch(content, /if \(false\) \{/, 'no gate-off skipped branch should remain in default build');
  } finally {
  }
});
