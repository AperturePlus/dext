import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

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

test('mountPanel: render shows controls + error text when bound (amend §7 formatError)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/panel.ts', 'panel.ts');
  try {
    const dom = fakeDom();
    const panel = mod.mountPanel(dom);
    panel.render({
      isBoundTab: true, bound: true, connected: true, autoMode: true, paused: false,
      phase: 'capturing', currentJob: { id: 'job-1', url: 'https://x.edu.cn/j', status: 'assigned', context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] }, created_at: 't', timeout_seconds: 60, action: null, identity_url: null },
      navigationAttempt: 1, lastError: { kind: 'rate_limited' }, pendingDecision: null,
    });
    assert.match(dom.shadowRoot.innerHTML, /data-cmd="unbind"/);
    assert.match(dom.shadowRoot.innerHTML, /rate_limited/);
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
