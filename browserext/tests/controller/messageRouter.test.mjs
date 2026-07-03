import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// Fake controller: records calls + holds a state the router reads via getState.
function fakeController(state) {
  const calls = { tick: 0, tickArgs: [], start: [], bind: [], retryCapture: [], reopenCurrentJob: 0, deliverPageReady: [], deliverCaptureResult: [], deliverActionPrepared: [], deliverActionResult: [], unbind: 0, setAutoMode: [], setPaused: [], manualComplete: 0, manualSkip: [], manualFail: [], overrideUrl: [], resolveDecision: [], broadcastPanelState: [] };
  return {
    calls,
    getNavScope: () => ({ boundTabId: state.boundTabId }),
    async getState() { return JSON.parse(JSON.stringify(state)); },
    async tick(...args) { calls.tick += 1; calls.tickArgs.push(args); },
    async start(tabId) { calls.start.push(tabId); return { started: true }; },
    async retryCapture(documentId) { calls.retryCapture.push(documentId); },
    async reopenCurrentJob() { calls.reopenCurrentJob += 1; },
    async bind(tabId) { calls.bind.push(tabId); },
    async unbind() { calls.unbind += 1; },
    async setAutoMode(v) { calls.setAutoMode.push(v); },
    async setPaused(v) { calls.setPaused.push(v); },
    async manualComplete() { calls.manualComplete += 1; },
    async manualSkip(r) { calls.manualSkip.push(r); },
    async manualFail(m) { calls.manualFail.push(m); },
    async overrideUrl(u) { calls.overrideUrl.push(u); },
    async resolveDecision(id, a) { calls.resolveDecision.push([id, a]); },
    async deliverPageReady(e) { calls.deliverPageReady.push(e); },
    async deliverCaptureResult(r, s) { calls.deliverCaptureResult.push([r, s]); },
    async deliverActionPrepared(p, s) { calls.deliverActionPrepared.push([p, s]); },
    async deliverActionResult(r, s) { calls.deliverActionResult.push([r, s]); },
    async broadcastPanelState(s) { calls.broadcastPanelState.push(s); },
    // slice-1..4 deliver* nav handlers — present but not exercised here
    async deliverBeforeRequest() {}, async deliverBeforeRedirect() {}, async deliverCommitted() {}, async deliverHttpEvent() {}, async deliverError() {},
    async dispatchPrepareAction() {},
  };
}

function fakeChrome() {
  const sent = [];
  let listener = null;
  return {
    sent,
    async sendMessage(tabId, message, options) { sent.push({ tabId, message, options }); return { received: true }; },
    onMessage(cb) { listener = cb; },
    fire(message, sender) { return listener ? listener(message, sender) : undefined; },
  };
}

const EXT_ID = 'ext-123';
const sender = (tabId, documentId = 'DOC-1') => ({ id: EXT_ID, tab: { id: tabId, url: 'https://xjtu.edu.cn/p' }, frameId: 0, documentId });

test('REGISTER from unbound tab → receipt with bound:false, isBoundTab:false (amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: null, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const reply = await chr.fire({ op: 'REGISTER', url: 'https://xjtu.edu.cn/p' }, sender(99));
    assert.equal(reply.received, true);
    assert.equal(reply.state.bound, false);
    assert.equal(reply.state.isBoundTab, false);
    assert.equal(ctrl.calls.tick, 0, 'REGISTER does not tick');
  } finally { await cleanup(); }
});

test('REGISTER from bound tab forces one backend probe before projecting state', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const base = { boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: true, paused: false, navigation: null, lastError: null, navigationAttempt: 0 };
    const ctrl = fakeController(base);
    let connected = false;
    ctrl.tick = async (...args) => {
      ctrl.calls.tick += 1;
      ctrl.calls.tickArgs.push(args);
      connected = true;
    };
    ctrl.getState = async () => ({ ...base, connected });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID, now: () => 1234 });
    router.start();
    const reply = await chr.fire({ op: 'REGISTER', url: 'https://xjtu.edu.cn/p' }, sender(42));
    assert.equal(ctrl.calls.tick, 1);
    assert.deepEqual(ctrl.calls.tickArgs, [[1234, { forceBackendProbe: true }]]);
    assert.equal(reply.state.connected, true);
  } finally { await cleanup(); }
});

test('REGISTER from non-bound tab remains read-only while another tab is bound', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: true, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const reply = await chr.fire({ op: 'REGISTER', url: 'https://xjtu.edu.cn/p' }, sender(99));
    assert.equal(ctrl.calls.tick, 0);
    assert.equal(reply.state.bound, true);
    assert.equal(reply.state.isBoundTab, false);
  } finally { await cleanup(); }
});

test('TICK from bound tab triggers controller.tick (amend §7); TICK from non-bound tab does NOT tick', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'TICK' }, sender(42));
    assert.equal(ctrl.calls.tick, 1);
    await chr.fire({ op: 'TICK' }, sender(99));
    assert.equal(ctrl.calls.tick, 1, 'non-bound TICK read-only');
  } finally { await cleanup(); }
});

test('bind command from unbound tab → controller.start called + broadcast (amend §4.7, §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: null, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'bind' } }, sender(99));
    assert.deepEqual(ctrl.calls.start, [99]);
    assert.equal(ctrl.calls.broadcastPanelState.length, 1);
  } finally { await cleanup(); }
});

test('retry/submit use the sender documentId; open performs a real reopen', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'error', currentJob: { id: 'job-1' }, connected: true, autoMode: true, paused: false, navigation: { attempt: 1 }, lastError: { kind: 'content_unavailable', missing: 'capture_result' }, navigationAttempt: 1 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    await router.handle({ op: 'COMMAND', command: { kind: 'retry_capture' } }, sender(42, 'DOC-LIVE'));
    await router.handle({ op: 'COMMAND', command: { kind: 'submit' } }, sender(42, 'DOC-LIVE'));
    await router.handle({ op: 'COMMAND', command: { kind: 'open' } }, sender(42, 'DOC-LIVE'));
    assert.deepEqual(ctrl.calls.retryCapture, ['DOC-LIVE', 'DOC-LIVE']);
    assert.equal(ctrl.calls.reopenCurrentJob, 1);
  } finally { await cleanup(); }
});

test('TICK returns state read after reconciliation and presents that fresh state', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const base = { boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: true, paused: false, navigation: null, lastError: null, navigationAttempt: 0 };
    const ctrl = fakeController(base);
    let connected = false;
    ctrl.tick = async () => { ctrl.calls.tick += 1; connected = true; };
    ctrl.getState = async () => ({ ...base, connected });
    const presented = [];
    const chr = fakeChrome();
    const router = mod.createMessageRouter({
      controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID,
      presentAction: async (_tabId, _url, state) => { presented.push(state.connected); },
    });
    router.start();
    const reply = await chr.fire({ op: 'TICK' }, sender(42));
    assert.equal(reply.state.connected, true);
    assert.deepEqual(presented, [true]);
  } finally { await cleanup(); }
});

test('non-bind command from unbound tab → refused (no-op, amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: null, pendingDecision: null, phase: 'idle', currentJob: null, connected: false, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'skip' } }, sender(99));
    assert.deepEqual(ctrl.calls.manualSkip, [], 'non-bind command from unbound tab refused');
  } finally { await cleanup(); }
});

test('command from non-bound tab when another tab is bound → refused (no takeover, amend §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'skip' } }, sender(99));
    assert.deepEqual(ctrl.calls.manualSkip, [], 'non-bound tab cannot issue skip while 42 is bound');
  } finally { await cleanup(); }
});

test('CAPTURE_RESULT routed to deliverCaptureResult with sender (amend §4.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'capturing', currentJob: { id: 'job-1' }, connected: true, autoMode: true, paused: false, navigation: { attempt: 1 }, lastError: null, navigationAttempt: 1 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const msg = { op: 'CAPTURE_RESULT', rpcId: 'rpc-1', jobId: 'job-1', ok: true, url: 'u', html: 'h', title: 't', paginationStates: [], detection: { errorPage: false, terminalReason: null } };
    await chr.fire(msg, sender(42, 'DOC-1'));
    assert.equal(ctrl.calls.deliverCaptureResult.length, 1);
    assert.deepEqual(ctrl.calls.deliverCaptureResult[0][1], { id: EXT_ID, tab: { id: 42, url: 'https://xjtu.edu.cn/p' }, frameId: 0, documentId: 'DOC-1' });
  } finally { await cleanup(); }
});

test('sender.id mismatch (not own extension) → no-op (amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const reply = await chr.fire({ op: 'TICK' }, { ...sender(42), id: 'other-ext' });
    assert.equal(reply, undefined, 'foreign-extension message dropped');
    assert.equal(ctrl.calls.tick, 0);
  } finally { await cleanup(); }
});

test('disallowed-host sender → no-op (defense in depth, amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: null, phase: 'idle', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    const offsiteSender = { id: EXT_ID, tab: { id: 42, url: 'https://evil.com/p' }, frameId: 0, documentId: 'DOC-1' };
    await chr.fire({ op: 'TICK' }, offsiteSender);
    assert.equal(ctrl.calls.tick, 0);
  } finally { await cleanup(); }
});

test('decision command → resolveDecision + broadcast (amend §4.9, §7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  try {
    const ctrl = fakeController({ boundTabId: 42, pendingDecision: { id: 'dec-1' }, phase: 'landed', currentJob: null, connected: true, autoMode: false, paused: false, navigation: null, lastError: null, navigationAttempt: 0 });
    const chr = fakeChrome();
    const router = mod.createMessageRouter({ controller: ctrl, chrome: chr, api: {}, extensionId: EXT_ID });
    router.start();
    await chr.fire({ op: 'COMMAND', command: { kind: 'decision', id: 'dec-1', action: 'accept' } }, sender(42));
    assert.deepEqual(ctrl.calls.resolveDecision, [['dec-1', 'accept']]);
    assert.equal(ctrl.calls.broadcastPanelState.length, 1);
  } finally { await cleanup(); }
});
