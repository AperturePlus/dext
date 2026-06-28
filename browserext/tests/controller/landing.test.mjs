import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function nav(overrides = {}) {
  return {
    jobId: 'job-1',
    requestedUrl: 'https://xjtu.edu.cn/faculty',
    issuedAt: 1000,
    attempt: 1,
    kind: 'navigate',
    commit: { documentId: 'DOC-1', committedUrl: 'https://xjtu.edu.cn/faculty', committedAt: 1100 },
    ...overrides,
  };
}
function state(navigation) {
  return {
    boundTabId: 7, boundAt: 1000, connected: true, autoMode: true, paused: false,
    currentJob: { id: 'job-1', url: 'https://xjtu.edu.cn/faculty' },
    phase: 'navigating', phaseStartedAt: 1000, navigation, pendingRpc: null,
    backendFailureCount: 0, nextBackendRetryAt: null, lastError: null,
  };
}
const CLEAN = { errorPage: false, terminalReason: null };

test('landed: commit + http ok (same requestId) + same-documentId clean PAGE_READY', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-1', documentId: 'DOC-1', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'https://xjtu.edu.cn/faculty', detection: CLEAN },
    }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'landed');
    assert.equal(v.acceptedUrl, 'https://xjtu.edu.cn/faculty');
    assert.equal(v.documentId, 'DOC-1');
  } finally { await cleanup(); }
});

test('landed: http with NO documentId still lands when requestId matches + clean PAGE_READY', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-1', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'https://xjtu.edu.cn/faculty', detection: CLEAN },
    }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'landed');
  } finally { await cleanup(); }
});

test('waiting: no navigation yet', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    assert.equal(mod.evaluateLanding(state(null), 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('waiting: commit present but no PAGE_READY / no http yet', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ requestId: 'REQ-1' }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('terminal_skip offsite_redirect: commit URL fails same-crawl-site gate', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ commit: { documentId: 'DOC-1', committedUrl: 'https://attacker.edu.cn/x', committedAt: 1100 } }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'terminal_skip');
    assert.equal(v.reason, 'offsite_redirect');
  } finally { await cleanup(); }
});

test('terminal_skip wechat_redirect: commit URL is a wechat host', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ commit: { documentId: 'DOC-1', committedUrl: 'https://mp.weixin.qq.com/s?x', committedAt: 1100 } }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'terminal_skip');
    assert.equal(v.reason, 'wechat_redirect');
  } finally { await cleanup(); }
});

test('terminal_skip not_found / content_removed / empty_page from PAGE_READY detection', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    for (const [reason, expected] of [['not_found','not_found'],['content_removed','content_removed'],['empty_page','empty_page']]) {
      const s = state(nav({ pageReady: { documentId: 'DOC-1', url: 'u', detection: { errorPage: false, terminalReason: reason } } }));
      const v = mod.evaluateLanding(s, 5000);
      assert.equal(v.kind, 'terminal_skip');
      assert.equal(v.reason, expected);
    }
  } finally { await cleanup(); }
});

test('error_retry gateway: PAGE_READY errorPage true on a navigate', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ pageReady: { documentId: 'DOC-1', url: 'u', detection: { errorPage: true, terminalReason: null } } }));
    const v = mod.evaluateLanding(s, 5000);
    assert.equal(v.kind, 'error_retry');
    assert.equal(v.outcome, 'gateway');
  } finally { await cleanup(); }
});

test('error_retry: http outcome not_found/gateway/rate_limited/unexpected_status/nav_error (requestId matched)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    for (const [outcome, code] of [['not_found',404],['gateway',502],['rate_limited',429],['unexpected_status',403],['nav_error',0]]) {
      const http = outcome === 'nav_error'
        ? { requestId: 'REQ-1', outcome: 'nav_error', error: 'ERR_X' }
        : { requestId: 'REQ-1', documentId: 'DOC-1', statusCode: code, outcome };
      const s = state(nav({ requestId: 'REQ-1', http, pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN } }));
      const v = mod.evaluateLanding(s, 5000);
      assert.equal(v.kind, 'error_retry', `${outcome} → error_retry`);
      assert.equal(v.outcome, outcome, `${outcome} outcome preserved`);
    }
  } finally { await cleanup(); }
});

test('error_retry unexpected_status carries statusCode detail; nav_error carries error detail', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s1 = state(nav({ requestId: 'REQ-1', http: { requestId: 'REQ-1', documentId: 'DOC-1', statusCode: 403, outcome: 'unexpected_status' }, pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN } }));
    const v1 = mod.evaluateLanding(s1, 5000);
    assert.equal(v1.kind, 'error_retry'); assert.equal(v1.detail, '403');
    const s2 = state(nav({ requestId: 'REQ-1', http: { requestId: 'REQ-1', outcome: 'nav_error', error: 'ERR_TIMED_OUT' }, pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN } }));
    const v2 = mod.evaluateLanding(s2, 5000);
    assert.equal(v2.kind, 'error_retry'); assert.equal(v2.detail, 'ERR_TIMED_OUT');
  } finally { await cleanup(); }
});

test('waiting: stale PAGE_READY whose documentId !== commit.documentId', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({ pageReady: { documentId: 'DOC-OLD', url: 'u', detection: CLEAN } }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('waiting: http documentId !== commit documentId (join fails; await correlated event)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-1', documentId: 'DOC-OTHER', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN },
    }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});

test('waiting: http from a different (stale) requestId — not yet cleared', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/landing.ts', 'landing.ts');
  try {
    const s = state(nav({
      requestId: 'REQ-1',
      http: { requestId: 'REQ-OLD', documentId: 'DOC-1', statusCode: 200, outcome: 'ok' },
      pageReady: { documentId: 'DOC-1', url: 'u', detection: CLEAN },
    }));
    assert.equal(mod.evaluateLanding(s, 5000).kind, 'waiting');
  } finally { await cleanup(); }
});
