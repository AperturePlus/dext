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

test('default build has EXCLUSIVE_CONTROL_ENABLED = false (gate off)', () => {
  rmSync(join(root, 'dist'), { recursive: true, force: true });
  try {
    build();
    const bg = readFileSync(distBg, 'utf8');
    // Controller guard `if (!GATE) return` folds to `if (true) return` — early return.
    assert.match(bg, /if \(true\) return/, 'controller must early-return when gate off');
    const content = readFileSync(distContent, 'utf8');
    // Content guard `if (GATE) {...}` folds to `if (false) {...}` — body skipped,
    // so the marker setAttribute is never reached (spec §6.1 true no-op).
    assert.match(content, /if \(false\) \{/, 'content body must be skipped when gate off');
    assert.doesNotMatch(content, /if \(true\)/, 'no gate-on branch should remain in default build');
  } finally {
  }
});
