import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function cleanDoc(o = {}) {
  return {
    title: 'Faculty',
    bodyText: 'a'.repeat(3000),
    readyState: 'complete',
    linksLength: 20,
    bodyNull: false,
    forms: { namedItem: () => null, length: 0 },
    querySelectorAll: () => [],
    querySelector: () => null,
    links: { length: 20 },
    location: { href: 'https://xjtu.edu.cn/faculty' },
    documentElement: { cloneNode: () => ({ outerHTML: '<html><body>captured</body></html>', querySelectorAll: () => [] }) },
    ...o,
  };
}
function fakeRuntime() {
  const sent = [];
  return {
    sent,
    sendMessage(msg) { sent.push(msg); return Promise.resolve({ received: true }); },
    onMessage() {},
  };
}

test('CAPTURE emits CAPTURE_RESULT with html + detection when page is clean (amend §2.3 second detection passes)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const router = mod.createRpcRouter({ document: cleanDoc(), runtime: rt });
    const receipt = await router.handle({ op: 'CAPTURE', rpcId: 'r1', jobId: 'j1' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.deepEqual(receipt, { received: true });
    assert.equal(rt.sent.length, 1);
    const m = rt.sent[0];
    assert.equal(m.op, 'CAPTURE_RESULT');
    assert.equal(m.ok, true);
    assert.ok(m.html.includes('captured'));
    assert.equal(m.detection.errorPage, false);
    assert.equal(m.detection.terminalReason, null);
  } finally { await cleanup(); }
});

test('CAPTURE second-detection fails (errorPage) → CAPTURE_RESULT ok:false, NO html (amend §2.3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const doc = cleanDoc({ title: '502 Bad Gateway', bodyText: 'x'.repeat(200) });   // errorPage true
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    await router.handle({ op: 'CAPTURE', rpcId: 'r1', jobId: 'j1' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const m = rt.sent[0];
    assert.equal(m.op, 'CAPTURE_RESULT');
    assert.equal(m.ok, false);
    assert.equal(m.html, undefined, 'no HTML on second-detection failure');
    assert.equal(m.detection.errorPage, true);
  } finally { await cleanup(); }
});

test('CAPTURE second-detection terminal not_found → CAPTURE_RESULT ok:false detection.terminalReason=not_found', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const doc = cleanDoc({ title: '404', bodyText: 'Not Found' });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    await router.handle({ op: 'CAPTURE', rpcId: 'r1', jobId: 'j1' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const m = rt.sent[0];
    assert.equal(m.ok, false);
    assert.equal(m.detection.terminalReason, 'not_found');
  } finally { await cleanup(); }
});

test('PERFORM_ACTION first call executes + caches; repeat does NOT re-execute (re-emits cached) (amend §4.3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    let submitCount = 0;
    const form = { name: 'pageForm', action: 'https://xjtu.edu.cn/list', method: 'get', enctype: '', target: '', _index: 0, id: null,
      elements: { namedItem: () => ({ value: '', type: 'text' }), length: 1, 0: { name: 'page', value: '1', type: 'text' } },
      submit() { submitCount += 1; } };
    const doc = cleanDoc({ forms: { namedItem: () => form, length: 1, 0: form }, querySelectorAll: () => [], location: { href: 'https://xjtu.edu.cn/list' } });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { page: '3' }, submit: true };
    // prepare first to get a fingerprint
    await router.handle({ op: 'PREPARE_ACTION', rpcId: 'r1', jobId: 'j1', action }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const prepared = rt.sent.at(-1);
    assert.equal(prepared.op, 'ACTION_PREPARED');
    assert.equal(prepared.ok, true);
    const fp = prepared.preparationFingerprint;
    // perform #1
    await router.handle({ op: 'PERFORM_ACTION', rpcId: 'r2', jobId: 'j1', action, preparationFingerprint: fp }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.equal(submitCount, 1, 'submitted once');
    // perform repeat (same rpcId) → no re-execute, re-emit cached
    await router.handle({ op: 'PERFORM_ACTION', rpcId: 'r2', jobId: 'j1', action, preparationFingerprint: fp }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.equal(submitCount, 1, 'NOT re-submitted on repeat');
    const last = rt.sent.at(-1);
    assert.equal(last.op, 'ACTION_RESULT');
  } finally { await cleanup(); }
});

test('PERFORM_ACTION fingerprint mismatch → ok:false error form_action_prepare_changed, NO submit (amend §5.3)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    let submitCount = 0;
    const form = { name: 'pageForm', action: 'https://xjtu.edu.cn/list', method: 'get', enctype: '', target: '', _index: 0, id: null,
      elements: { namedItem: () => ({ value: '', type: 'text' }), length: 1, 0: { name: 'page', value: '1', type: 'text' } },
      submit() { submitCount += 1; } };
    const doc = cleanDoc({ forms: { namedItem: () => form, length: 1, 0: form }, location: { href: 'https://xjtu.edu.cn/list' } });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { page: '3' }, submit: true };
    await router.handle({ op: 'PERFORM_ACTION', rpcId: 'r2', jobId: 'j1', action, preparationFingerprint: 'WRONG-FP' }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    assert.equal(submitCount, 0, 'no submit on fingerprint mismatch');
    const m = rt.sent.at(-1);
    assert.equal(m.op, 'ACTION_RESULT');
    assert.equal(m.ok, false);
    assert.equal(m.error, 'form_action_prepare_changed');
    assert.equal(m.invoked, false);
  } finally { await cleanup(); }
});

test('PREPARE_ACTION offsite target → ACTION_PREPARED ok:false error offsite_redirect (amend §5.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const form = { name: 'pageForm', action: 'https://attacker.edu.cn/steal', method: 'post', enctype: '', target: '', _index: 0, id: null,
      elements: { namedItem: () => null, length: 0 } };
    const doc = cleanDoc({ forms: { namedItem: () => form, length: 1, 0: form }, location: { href: 'https://xjtu.edu.cn/list' } });
    const router = mod.createRpcRouter({ document: doc, runtime: rt });
    await router.handle({ op: 'PREPARE_ACTION', rpcId: 'r1', jobId: 'j1', action: { kind: 'form_submit', form_name: 'pageForm', fields: {}, submit: true } }, { tab: { id: 1 }, frameId: 0, documentId: 'DOC-1' });
    const m = rt.sent.at(-1);
    assert.equal(m.op, 'ACTION_PREPARED');
    assert.equal(m.ok, false);
    assert.equal(m.error, 'offsite_redirect');
  } finally { await cleanup(); }
});

test('handle always returns { received: true } (transport receipt; amend §1.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/rpcRouter.ts', 'rpcRouter.ts');
  try {
    const rt = fakeRuntime();
    const router = mod.createRpcRouter({ document: cleanDoc(), runtime: rt });
    for (const op of ['PREPARE_ACTION', 'PERFORM_ACTION', 'CAPTURE']) {
      const msg = op === 'CAPTURE' ? { op, rpcId: 'r', jobId: 'j' } : { op, rpcId: 'r', jobId: 'j', action: { kind: 'form_submit', form_name: 'x', fields: {} } };
      if (op === 'PERFORM_ACTION') msg.preparationFingerprint = 'x';
      const receipt = await router.handle(msg, { tab: { id: 1 }, frameId: 0, documentId: 'D' });
      assert.deepEqual(receipt, { received: true });
    }
  } finally { await cleanup(); }
});
