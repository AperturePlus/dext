import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// harness injects EXCLUSIVE_CONTROL_ENABLED = true.

function fakeDoc(hostname) {
  const attrs = new Map();
  const el = {
    getAttribute: (n) => (attrs.has(n) ? attrs.get(n) : null),
    setAttribute: (n, v) => attrs.set(n, v),
    removeAttribute: (n) => attrs.delete(n),
    _has: (n) => attrs.has(n),
    _get: (n) => attrs.get(n),
  };
  return { el, hostname };
}

test('work RPC listener sends transport receipt synchronously before async work', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const order = [];
    let finishWork;
    const router = {
      handle() {
        order.push('work-started');
        return new Promise((resolve) => { finishWork = () => { order.push('work-finished'); resolve({ received: true }); }; });
      },
    };
    const listener = mod.createWorkMessageListener(router);
    const keepOpen = listener(
      { op: 'CAPTURE', rpcId: 'rpc-1', jobId: 'job-1' },
      { frameId: 0, documentId: 'DOC-1' },
      (receipt) => { order.push('receipt'); assert.deepEqual(receipt, { received: true }); },
    );
    assert.equal(keepOpen, false, 'synchronous sendResponse does not keep the port open');
    assert.deepEqual(order, ['receipt'], 'receipt is delivered before any DOM work starts');
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.deepEqual(order, ['receipt', 'work-started']);
    finishWork();
    await new Promise((resolve) => setImmediate(resolve));
    assert.deepEqual(order, ['receipt', 'work-started', 'work-finished']);
  } finally { await cleanup(); }
});

test('gated ON: sets data-dext-extension-controller=v1 on allowed host', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('xjtu.edu.cn');
    await mod.bootstrapContent({ hostname: doc.hostname, documentElement: doc.el, now: 1000 });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
  } finally { await cleanup(); }
});

test('gated ON: sets marker even on disallowed host, then early-returns (no onAllowedHost)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('mp.weixin.qq.com');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    // marker is set on EVERY page (spec §5.2), but disallowed host must not proceed
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, false, 'disallowed host must early-return before onAllowedHost');
  } finally { await cleanup(); }
});

test('gated ON: allowed host proceeds to onAllowedHost hook (slice 1 stops there)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('foo.github.io');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, true);
  } finally { await cleanup(); }
});

test('bootstrapContent (gate ON): allowed host mounts panel, sends REGISTER, wires STATE_CHANGED (slice 5)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const sent = [];
    let stateChangedCb = null;
    const attrs = new Map();
    const documentElement = { getAttribute: (n) => attrs.get(n) ?? null, setAttribute: (n, v) => attrs.set(n, v), removeAttribute: (n) => attrs.delete(n) };
    const shadowRoot = { innerHTML: '', querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} };
    const fakeDoc = {
      readyState: 'complete', body: { innerText: 'x', appendChild() {} }, title: 't',
      links: { length: 1 }, forms: [], documentElement,
      addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
      createElement: () => ({ attachShadow: () => shadowRoot, style: {}, appendChild() {}, id: '' }),
    };
    const fakeChrome = {
      runtime: {
        sendMessage: (m) => { sent.push(m); return Promise.resolve({ received: true, state: undefined }); },
        onMessage: { addListener: (cb) => { stateChangedCb = cb; } },
        id: 'ext-123',
      },
    };
    await mod.bootstrapContent({ hostname: 'xjtu.edu.cn', documentElement, now: 1000, document: fakeDoc, chrome: fakeChrome });
    assert.ok(sent.some((m) => m.op === 'REGISTER'), 'REGISTER sent on allowed host');
    assert.equal(typeof stateChangedCb, 'function', 'STATE_CHANGED listener wired');
  } finally { await cleanup(); }
});

test('bootstrapContent (gate ON): slice-1/4 tests without document/chrome skip panel block unchanged (slice 5)', async () => {
  // Regression guard: the existing slice-1/4 tests call bootstrapContent with NO
  // document/chrome. The panel block must be GUARDED so those calls do NOT crash
  // (they simply skip the panel mount).
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const doc = fakeDoc('foo.github.io');
    let proceeded = false;
    await mod.bootstrapContent({
      hostname: doc.hostname, documentElement: doc.el, now: 1000,
      onAllowedHost: () => { proceeded = true; },
    });
    assert.equal(doc.el._get('data-dext-extension-controller'), 'v1');
    assert.equal(proceeded, true);
  } finally { await cleanup(); }
});

test('bootstrapContent defers panel until DOMContentLoaded when document_start has no body', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  try {
    const sent = [];
    let domReady = null;
    let appended = 0;
    const attrs = new Map();
    const documentElement = { getAttribute: (n) => attrs.get(n) ?? null, setAttribute: (n, v) => attrs.set(n, v), removeAttribute: (n) => attrs.delete(n) };
    const shadowRoot = { innerHTML: '', querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} };
    const fakeDoc = {
      readyState: 'loading', body: null, title: 't', links: { length: 0 }, forms: [], documentElement,
      addEventListener(type, cb) { if (type === 'DOMContentLoaded') domReady = cb; },
      querySelector() { return null; }, querySelectorAll() { return []; },
      createElement: () => ({ attachShadow: () => shadowRoot, id: '' }),
    };
    const fakeChrome = {
      runtime: {
        sendMessage: async (m) => { sent.push(m); return { received: true, state: undefined }; },
        onMessage: { addListener() {} }, id: 'ext-123',
      },
    };
    await mod.bootstrapContent({ hostname: 'xjtu.edu.cn', documentElement, document: fakeDoc, chrome: fakeChrome });
    assert.equal(sent.some((m) => m.op === 'REGISTER'), false);
    fakeDoc.body = { innerText: 'x', appendChild() { appended += 1; } };
    domReady();
    domReady();
    await new Promise((resolve) => setImmediate(resolve));
    assert.equal(appended, 1, 'panel host mounted once');
    assert.equal(sent.filter((m) => m.op === 'REGISTER').length, 1);
  } finally { await cleanup(); }
});

test('panel renders the fresh state returned by each TICK receipt', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/index.ts', 'index.ts');
  const realSetInterval = globalThis.setInterval;
  try {
    let interval = null;
    globalThis.setInterval = (cb) => { interval = cb; return { unref() {} }; };
    const attrs = new Map();
    const documentElement = { getAttribute: (n) => attrs.get(n) ?? null, setAttribute: (n, v) => attrs.set(n, v), removeAttribute: (n) => attrs.delete(n) };
    const shadowRoot = { innerHTML: '', querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener() {} };
    const fakeDoc = {
      readyState: 'complete', body: { innerText: 'x', appendChild() {} }, title: 't', links: { length: 1 }, forms: [], documentElement,
      addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
      createElement: () => ({ attachShadow: () => shadowRoot, id: '' }),
    };
    const panelState = (connected) => ({
      isBoundTab: true, bound: true, connected, autoMode: true, paused: false,
      phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null,
    });
    const fakeChrome = {
      runtime: {
        sendMessage: async (m) => ({ received: true, state: panelState(m.op === 'TICK') }),
        onMessage: { addListener() {} }, id: 'ext-123',
      },
    };
    await mod.bootstrapContent({ hostname: 'xjtu.edu.cn', documentElement, document: fakeDoc, chrome: fakeChrome });
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(shadowRoot.innerHTML, /后端离线/);
    interval();
    await new Promise((resolve) => setImmediate(resolve));
    assert.match(shadowRoot.innerHTML, /已连接/);
  } finally {
    globalThis.setInterval = realSetInterval;
    await cleanup();
  }
});
