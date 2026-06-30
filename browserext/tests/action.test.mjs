import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function state(overrides = {}) {
  return {
    boundTabId: null, boundAt: null, connected: false, autoMode: false, paused: false,
    currentJob: null, phase: 'idle', phaseStartedAt: 0, navigation: null,
    pendingRpc: null, backendFailureCount: 0, nextBackendRetryAt: null,
    lastError: null, pendingDecision: null, ...overrides,
  };
}

function fakeChrome() {
  const calls = { badge: [], color: [], title: [] };
  let clicked = null;
  return {
    calls,
    onClicked(cb) { clicked = cb; },
    fire(tab) { return clicked?.(tab); },
    async setBadgeText(v) { calls.badge.push(v); },
    async setBadgeBackgroundColor(v) { calls.color.push(v); },
    async setTitle(v) { calls.title.push(v); },
  };
}

test('actionStatusFor maps ready/ON/ERR/BUSY/N/A per tab', async () => {
  const { mod, cleanup } = await importTsModule('../src/action.ts', 'action.ts');
  try {
    const url = 'https://gr.xjtu.edu.cn/';
    assert.equal(mod.actionStatusFor(state(), 42, url), 'ready');
    assert.equal(mod.actionStatusFor(state({ boundTabId: 42, connected: true }), 42, url), 'on');
    assert.equal(mod.actionStatusFor(state({ boundTabId: 42, connected: false }), 42, url), 'err');
    assert.equal(mod.actionStatusFor(state({ boundTabId: 99 }), 42, url), 'busy');
    assert.equal(mod.actionStatusFor(state(), 42, 'https://example.com/'), 'unsupported');
  } finally { await cleanup(); }
});

test('toolbar click starts an allowed unbound tab and presents ON', async () => {
  const { mod, cleanup } = await importTsModule('../src/action.ts', 'action.ts');
  try {
    let current = state();
    const calls = { start: [], broadcast: [] };
    const controller = {
      async getState() { return structuredClone(current); },
      async start(tabId, now) {
        calls.start.push([tabId, now]);
        current = state({ boundTabId: tabId, boundAt: now, connected: true, autoMode: true });
        return { started: true };
      },
      async broadcastPanelState(sender) { calls.broadcast.push(sender); },
    };
    const chrome = fakeChrome();
    const action = mod.createToolbarAction({ controller, chrome, now: () => 1234 });
    const status = await action.handleClick({ id: 42, url: 'https://gr.xjtu.edu.cn/' });
    assert.equal(status, 'on');
    assert.deepEqual(calls.start, [[42, 1234]]);
    assert.deepEqual(calls.broadcast, [{ tabId: 42 }]);
    assert.equal(chrome.calls.badge.at(-1).text, 'ON');
  } finally { await cleanup(); }
});

test('toolbar click resumes the same tab but never preempts another binding', async () => {
  const { mod, cleanup } = await importTsModule('../src/action.ts', 'action.ts');
  try {
    const calls = [];
    let current = state({ boundTabId: 42, connected: false, paused: true });
    const controller = {
      async getState() { return structuredClone(current); },
      async start(tabId) { calls.push(tabId); current = state({ boundTabId: tabId, connected: false, autoMode: true }); return { started: true }; },
      async broadcastPanelState() {},
    };
    const action = mod.createToolbarAction({ controller, chrome: fakeChrome() });
    assert.equal(await action.handleClick({ id: 42, url: 'https://gr.xjtu.edu.cn/' }), 'err');
    current = state({ boundTabId: 99, connected: true });
    assert.equal(await action.handleClick({ id: 42, url: 'https://gr.xjtu.edu.cn/' }), 'busy');
    assert.deepEqual(calls, [42], 'other binding was not preempted');
  } finally { await cleanup(); }
});

test('toolbar click on an unsupported URL has no controller side effect and shows N/A', async () => {
  const { mod, cleanup } = await importTsModule('../src/action.ts', 'action.ts');
  try {
    let starts = 0;
    const chrome = fakeChrome();
    const action = mod.createToolbarAction({
      controller: {
        async getState() { return state(); },
        async start() { starts += 1; return { started: true }; },
        async broadcastPanelState() {},
      },
      chrome,
    });
    assert.equal(await action.handleClick({ id: 42, url: 'https://example.com/' }), 'unsupported');
    assert.equal(starts, 0);
    assert.equal(chrome.calls.badge.at(-1).text, 'N/A');
  } finally { await cleanup(); }
});
