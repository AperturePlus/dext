import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function state(navigation) {
  return {
    boundTabId: 7, boundAt: 1000, connected: true, autoMode: true, paused: false,
    currentJob: { id: 'job-1', url: 'https://x.edu.cn/p' },
    phase: 'navigating', phaseStartedAt: 1000, navigation, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
  };
}

test('none: no navigation', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    assert.equal(mod.navTimeoutKind(state(null), 99999).kind, 'none');
  } finally { await cleanup(); }
});

test('none: navigation with no commit, within 30s (now < issuedAt+30s)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate' });
    assert.equal(mod.navTimeoutKind(s, 30999).kind, 'none');   // 1000+30000 = 31000; 30999 < 31000
  } finally { await cleanup(); }
});

test('never_landed: no commit past navigationDeadlineAt (now >= issuedAt+30s)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate' });
    assert.equal(mod.navTimeoutKind(s, 31000).kind, 'never_landed');   // 31000 >= 31000
  } finally { await cleanup(); }
});

test('none: commit present, within landingSignalsDeadlineAt (now < committedAt+30s)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 } });
    assert.equal(mod.navTimeoutKind(s, 31499).kind, 'none');   // 1500+30000 = 31500; 31499 < 31500
  } finally { await cleanup(); }
});

test('content_unavailable/page_ready: commit present, PAGE_READY missing, now >= committedAt+30s', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 } });
    const v = mod.navTimeoutKind(s, 31500);
    assert.equal(v.kind, 'content_unavailable');
    assert.equal(v.missing, 'page_ready');
    assert.equal(v.sourceDocumentId, 'DOC-1');
  } finally { await cleanup(); }
});

test('content_unavailable/http_outcome: commit + PAGE_READY present, HTTP missing, now >= committedAt+30s', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 },
      pageReady: { documentId: 'DOC-1', url: 'u', detection: { errorPage: false, terminalReason: null } } });
    const v = mod.navTimeoutKind(s, 31500);
    assert.equal(v.kind, 'content_unavailable');
    assert.equal(v.missing, 'http_outcome');
  } finally { await cleanup(); }
});

test('content_unavailable prioritizes page_ready when both missing', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/deadline.ts', 'deadline.ts');
  try {
    const s = state({ jobId: 'job-1', requestedUrl: 'u', issuedAt: 1000, attempt: 1, kind: 'navigate',
      commit: { documentId: 'DOC-1', committedUrl: 'u', committedAt: 1500 } });
    assert.equal(mod.navTimeoutKind(s, 31500).missing, 'page_ready');
  } finally { await cleanup(); }
});
