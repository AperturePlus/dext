import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

test('panel CSS pins the controller visibly above the host page', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.match(mod.PANEL_CSS, /:host\s*\{/);
    assert.match(mod.PANEL_CSS, /position:\s*fixed/);
    assert.match(mod.PANEL_CSS, /z-index:\s*2147483647/);
  } finally { await cleanup(); }
});

// Minimal fake DOM good enough for mountPanel + commandFromClick.
// `host.attachShadow` increments `host.attachShadowCalls` and returns the
// shared shadowRoot so the idempotency test can assert attachShadow is
// called at most once across repeated mountPanel calls.
function fakeDom() {
  const el = (id) => ({
    id, dataset: {}, style: {}, textContent: '', innerHTML: '',
    classList: { add() {}, remove() {}, toggle() {} },
    addEventListener() {}, appendChild() {}, querySelector() { return null; },
    querySelectorAll() { return []; }, setAttribute() {}, getAttribute() { return null; },
    remove() {}, cloneNode() { return { outerHTML: '', querySelectorAll() { return []; } }; },
  });
  const shadowRoot = {
    innerHTML: '',
    querySelector() { return null; }, querySelectorAll() { return []; },
    getElementById(id) { return els[id] ?? null; }, addEventListener() {},
  };
  const host = {
    ...el('dext-panel-host'),
    attachShadowCalls: 0,
    attachShadow() { host.attachShadowCalls += 1; return shadowRoot; },
  };
  const els = {};
  const document = {
    createElement: (tag) => { const e = el(tag); return e; },
    getElementById: (id) => els[id] ?? null,
    body: { appendChild: (n) => { if (n && n.id) els[n.id] = n; } },
  };
  return { host, shadowRoot, document, els, register: (id) => { els[id] = el(id); return els[id]; } };
}

test('commandFromClick: data-cmd=bind → bind command (unbound only allowed op, amend §4.7)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const cmd = mod.commandFromClick({ dataset: { cmd: 'bind' } }, () => null);
    assert.deepEqual(cmd, { kind: 'bind' });
  } finally { await cleanup(); }
});

test('commandFromClick: data-cmd=override with override-url input value', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const cmd = mod.commandFromClick({ dataset: { cmd: 'override' } }, (id) => id === 'dext-override-url' ? 'https://x.edu.cn/new' : null);
    assert.deepEqual(cmd, { kind: 'override', url: 'https://x.edu.cn/new' });
  } finally { await cleanup(); }
});

test('commandFromClick: data-cmd=decision carries id+action', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const cmd = mod.commandFromClick({ dataset: { cmd: 'decision', id: 'dec-1', action: 'skip' } }, () => null);
    assert.deepEqual(cmd, { kind: 'decision', id: 'dec-1', action: 'skip' });
  } finally { await cleanup(); }
});

test('commandFromClick: non-command click → null', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.equal(mod.commandFromClick({ dataset: {} }, () => null), null);
    assert.equal(mod.commandFromClick(null, () => null), null);
  } finally { await cleanup(); }
});

test('commandFromClick: set_auto/set_paused carry value from dataset', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.deepEqual(mod.commandFromClick({ dataset: { cmd: 'set_auto', value: 'true' } }, () => null), { kind: 'set_auto', value: true });
    assert.deepEqual(mod.commandFromClick({ dataset: { cmd: 'set_paused', value: 'false' } }, () => null), { kind: 'set_paused', value: false });
  } finally { await cleanup(); }
});

test('commandFromClick: retry_capture is a distinct recovery command', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.deepEqual(mod.commandFromClick({ dataset: { cmd: 'retry_capture' } }, () => null), { kind: 'retry_capture' });
  } finally { await cleanup(); }
});

test('mountPanel: render shows bind button when unbound (isBoundTab false, bound false)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    const unbound = { isBoundTab: false, bound: false, connected: true, autoMode: false, paused: false, phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null };
    panel.render(unbound);
    // The shadow root receives an innerHTML string containing a bind control.
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="bind"/);
  } finally { await cleanup(); }
});

test('mountPanel renders approved dark card, progress, target and capture recovery action', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: true, paused: false,
      phase: 'capturing', currentJob: { id: 'job-1', url: 'https://x.edu.cn/j', status: 'assigned', context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] }, created_at: 't', timeout_seconds: 60, action: null, identity_url: null },
      navigationAttempt: 1, lastError: { kind: 'content_unavailable', missing: 'capture_result', sourceDocumentId: 'DOC-1', since: 1, recoveryAttempts: 3, nextRecoveryAt: null, recoveryExhausted: true }, pendingDecision: null,
    });
    assert.match(dom.shadowRoot.innerHTML, /dext crawler/);
    assert.match(dom.shadowRoot.innerHTML, /导航/);
    assert.match(dom.shadowRoot.innerHTML, /捕获/);
    assert.match(dom.shadowRoot.innerHTML, /提交/);
    assert.match(dom.shadowRoot.innerHTML, /X/);
    assert.match(dom.shadowRoot.innerHTML, /x\.edu\.cn\/j/);
    assert.match(dom.shadowRoot.innerHTML, /title="https:\/\/x\.edu\.cn\/j"/);
    assert.match(dom.shadowRoot.innerHTML, /页面捕获未返回/);
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="retry_capture"/);
    assert.match(dom.shadowRoot.innerHTML, /data-ui="more"/);
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="unbind"/);
  } finally { await cleanup(); }
});

test('panel CSS has 360px desktop sizing, narrow-screen adaptation and focus visibility', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    assert.match(mod.PANEL_CSS, /360px/);
    assert.match(mod.PANEL_CSS, /@media\s*\(max-width:/);
    assert.match(mod.PANEL_CSS, /:focus-visible/);
  } finally { await cleanup(); }
});

test('mountPanel local collapse and more controls rerender without sending commands', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    let clickCb = null;
    const shadowRoot = { innerHTML: '', querySelector() { return null; }, querySelectorAll() { return []; }, addEventListener(type, cb) { if (type === 'click') clickCb = cb; } };
    const host = { attachShadow() { return shadowRoot; } };
    const commands = [];
    const panel = mod.mountPanel({ host }, (cmd) => commands.push(cmd));
    panel.render({ isBoundTab: true, bound: true, connected: true, autoMode: true, paused: false, phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null });
    clickCb({ target: { dataset: { ui: 'more' } } });
    assert.match(shadowRoot.innerHTML, /data-cmd="skip"/);
    clickCb({ target: { dataset: { ui: 'collapse' } } });
    assert.match(shadowRoot.innerHTML, /dext-card is-collapsed/);
    assert.doesNotMatch(shadowRoot.innerHTML, /data-cmd="submit"/);
    assert.deepEqual(commands, []);
  } finally { await cleanup(); }
});

test('mountPanel: render inlines pending-decision controls (no window.confirm, amend §4.9)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false,
      phase: 'landed', currentJob: null, navigationAttempt: 0, lastError: null,
      pendingDecision: { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 3, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' },
    });
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="decision"/);
    assert.match(dom.shadowRoot.innerHTML, /dec-1/);
    // window.confirm is forbidden (amend §4.9) — the panel must not reference it.
    assert.doesNotMatch(mod.PANEL_CSS + dom.shadowRoot.innerHTML, /window\.confirm/);
  } finally { await cleanup(); }
});

test('mountPanel is idempotent — second mount reuses the same shadow root', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    mod.mountPanel(dom);
    const calls = dom.host.attachShadowCalls ?? 0;
    mod.mountPanel(dom);
    // attachShadow called at most once across both mounts.
    assert.ok((dom.host.attachShadowCalls ?? 0) <= 1);
    void calls;
  } finally { await cleanup(); }
});

test('mountPanel: override command honors injected formData (Task-7 fix, amend §7)', async () => {
  // Regression guard: Task-3 mountPanel passed `() => null` to commandFromClick,
  // which made the override command DEAD from real clicks (the input value was
  // never read). Task 7 adds an optional `formData` injection point to mountPanel
  // so the click listener reads the live override-URL input. This test pins that
  // the injected formData is honored by the click listener — NOT `() => null`.
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const overrideInput = { value: 'https://x.edu.cn/override-target' };
    // Custom fake: shadow root records the click listener so we can dispatch it.
    let clickCb = null;
    const shadowRoot = {
      innerHTML: '',
      querySelector() { return null; }, querySelectorAll() { return []; },
      getElementById(id) { return id === 'dext-override-url' ? overrideInput : null; },
      addEventListener(type, cb) { if (type === 'click') clickCb = cb; },
    };
    const host = { attachShadowCalls: 0, attachShadow() { host.attachShadowCalls += 1; return shadowRoot; } };
    const captured = [];
    const panel = mod.mountPanel({ host }, (cmd) => { captured.push(cmd); }, (id) => {
      const el = shadowRoot.getElementById(id);
      return el?.value ?? null;
    });
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false,
      phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null,
    });
    // Dispatch a click on the override button dataset.
    assert.equal(typeof clickCb, 'function', 'click listener registered');
    clickCb({ target: { dataset: { cmd: 'override' } } });
    assert.equal(captured.length, 1, 'onCommand fired once for override');
    assert.deepEqual(captured[0], { kind: 'override', url: 'https://x.edu.cn/override-target' });
  } finally { await cleanup(); }
});

test('mountPanel: override command returns DEAD when no formData injected (back-compat, Task-7)', async () => {
  // Back-compat: when no formData is passed, mountPanel falls back to `() => null`
  // so the override command stays a no-op (matches Task-3 behavior for callers
  // that don't opt into the formData injection).
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    let clickCb = null;
    const shadowRoot = {
      innerHTML: '',
      querySelector() { return null; }, querySelectorAll() { return []; },
      getElementById() { return null; },
      addEventListener(type, cb) { if (type === 'click') clickCb = cb; },
    };
    const host = { attachShadowCalls: 0, attachShadow() { host.attachShadowCalls += 1; return shadowRoot; } };
    const captured = [];
    const panel = mod.mountPanel({ host }, (cmd) => { captured.push(cmd); });
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: false, paused: false,
      phase: 'idle', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null,
    });
    clickCb({ target: { dataset: { cmd: 'override' } } });
    assert.equal(captured.length, 0, 'override stays dead without formData (back-compat)');
  } finally { await cleanup(); }
});

test('mountPanel: render shows "已绑定其他标签" when bound but not the bound tab (amend §7 non-preemptible, slice-6 coverage)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: false, bound: true, connected: true, autoMode: false, paused: false,
      phase: 'navigating', currentJob: null, navigationAttempt: 0, lastError: null, pendingDecision: null,
    });
    // The non-bound-tab branch shows the muted "already bound elsewhere" text.
    assert.match(dom.shadowRoot.innerHTML, /已绑定其他标签/);
    // It must NOT render any command controls (no bind, no unbind, no submit/skip/fail)
    // — a non-bound tab cannot take over or issue commands (amend §7 non-preemptible).
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="bind"/);
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="unbind"/);
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="submit"/);
    assert.doesNotMatch(dom.shadowRoot.innerHTML, /data-cmd="skip"/);
  } finally { await cleanup(); }
});
