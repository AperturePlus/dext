import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

// A NavController fake that records every delivery (amend §8.1 #17: navMonitor
// forwards raw event + outcome for all 6 outcomes; it never decides/acts itself).
function fakeController(boundTabId = 1) {
  const calls = { beforeRequest: [], beforeRedirect: [], committed: [], http: [], error: [], pageReady: [], scopeReads: 0 };
  return {
    calls,
    getNavScope() { calls.scopeReads += 1; return { boundTabId }; },
    async deliverBeforeRequest(e) { calls.beforeRequest.push(e); },
    async deliverBeforeRedirect(e) { calls.beforeRedirect.push(e); },
    async deliverCommitted(e) { calls.committed.push(e); },
    async deliverHttpEvent(e, o) { calls.http.push({ e, o }); },
    async deliverError(e, o) { calls.error.push({ e, o }); },
    async deliverPageReady(e) { calls.pageReady.push(e); },
  };
}

function fakeChrome() {
  const cbs = { beforeRequest: [], beforeRedirect: [], committed: [], history: [], completed: [], error: [] };
  return {
    onBeforeRequest(cb) { cbs.beforeRequest.push(cb); },
    onBeforeRedirect(cb) { cbs.beforeRedirect.push(cb); },
    onCommitted(cb) { cbs.committed.push(cb); },
    onHistoryStateUpdated(cb) { cbs.history.push(cb); },
    onNavCompleted(cb) { cbs.completed.push(cb); },
    onNavError(cb) { cbs.error.push(cb); },
    fire(kind, e) { for (const cb of cbs[kind]) cb(e); },
  };
}

test('navMonitor accepts only {chrome, controller} deps (thin adapter; no api/storage)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const nm = mod.createNavMonitor({ chrome: fakeChrome(), controller: fakeController() });
    nm.start();
    assert.equal(typeof nm.start, 'function');
  } finally {
    await cleanup();
  }
});

test('onNavCompleted forwards raw event + classifyNavigation outcome for every NavOutcome (amend §8.1 #17)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    const cases = [
      [200, 'ok'], [304, 'ok'], [404, 'not_found'], [410, 'not_found'], [429, 'rate_limited'],
      [500, 'gateway'], [502, 'gateway'], [503, 'gateway'], [504, 'gateway'],
      [204, 'unexpected_status'], [401, 'unexpected_status'], [403, 'unexpected_status'], [301, 'unexpected_status'],
    ];
    for (const [code, expectedOutcome] of cases) {
      chr.fire('completed', { tabId: 1, url: 'https://x.edu.cn/p', statusCode: code, frameId: 0, requestId: 'R', documentId: 'D', timeStamp: 100 });
      assert.equal(ctrl.calls.http.at(-1).o, expectedOutcome, `${code} → ${expectedOutcome}`);
      assert.equal(ctrl.calls.http.at(-1).e.statusCode, code);
      assert.equal(ctrl.calls.http.at(-1).e.requestId, 'R');
    }
    assert.equal(ctrl.calls.http.length, cases.length);
  } finally {
    await cleanup();
  }
});

test('onNavError forwards raw event + nav_error outcome', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('error', { tabId: 1, url: 'https://x.edu.cn/p', error: 'ERR_CONNECTION_REFUSED', frameId: 0, requestId: 'R', timeStamp: 100 });
    assert.equal(ctrl.calls.error.length, 1);
    assert.equal(ctrl.calls.error[0].o, 'nav_error');
    assert.equal(ctrl.calls.error[0].e.error, 'ERR_CONNECTION_REFUSED');
  } finally {
    await cleanup();
  }
});

test('onBeforeRedirect forwards raw, NO classifier call (amend §8.1 #14)', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('beforeRedirect', { tabId: 1, frameId: 0, type: 'main_frame', url: 'https://x.edu.cn/p', redirectUrl: 'https://attacker.edu.cn/x', requestId: 'R', timeStamp: 100 });
    assert.equal(ctrl.calls.beforeRedirect.length, 1);
    assert.equal(ctrl.calls.beforeRedirect[0].redirectUrl, 'https://attacker.edu.cn/x');
    assert.equal(ctrl.calls.http.length, 0, 'onBeforeRedirect must not call the classifier / not deliver an http outcome');
  } finally {
    await cleanup();
  }
});

test('scope filter: sub-frame (frameId !== 0) events are dropped', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('completed', { tabId: 1, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 2, requestId: 'R', timeStamp: 100 });
    chr.fire('committed', { tabId: 1, frameId: 2, documentId: 'D', url: 'u', timeStamp: 100 });
    assert.equal(ctrl.calls.http.length, 0);
    assert.equal(ctrl.calls.committed.length, 0);
  } finally {
    await cleanup();
  }
});

test('scope filter: non-bound-tab events are dropped', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController(1);
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('completed', { tabId: 999, url: 'https://x.edu.cn/p', statusCode: 404, frameId: 0, requestId: 'R', timeStamp: 100 });
    assert.equal(ctrl.calls.http.length, 0, 'non-bound-tab event dropped');
  } finally {
    await cleanup();
  }
});

test('beforeRequest + committed forwarded raw', async () => {
  const { mod, cleanup } = await importTsModule('../src/navMonitor.ts', 'navMonitor.ts');
  try {
    const ctrl = fakeController();
    const chr = fakeChrome();
    mod.createNavMonitor({ chrome: chr, controller: ctrl }).start();
    chr.fire('beforeRequest', { tabId: 1, frameId: 0, type: 'main_frame', url: 'https://x.edu.cn/p', requestId: 'R', timeStamp: 100 });
    chr.fire('committed', { tabId: 1, frameId: 0, documentId: 'D', url: 'https://x.edu.cn/p', timeStamp: 110 });
    assert.equal(ctrl.calls.beforeRequest[0].requestId, 'R');
    assert.equal(ctrl.calls.committed[0].documentId, 'D');
  } finally {
    await cleanup();
  }
});
