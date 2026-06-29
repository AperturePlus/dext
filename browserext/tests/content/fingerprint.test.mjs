import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function form(o = {}) {
  return {
    _index: 0, name: 'pageForm', id: null, action: '', method: 'get', enctype: '', target: '',
    elements: [],
    ...o,
  };
}
function elt(name, type, value, extra = {}) { return { name, type, value, ...extra }; }

test('canonicalizeActionSnapshot is deterministic + keys are sorted', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const f = form({ elements: [elt('b', 'text', '2'), elt('a', 'text', '1')] });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { b: '5' }, submit: true };
    const s1 = mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } });
    const s2 = mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } });
    assert.equal(s1, s2, 'deterministic');
    // action.fields sorted by key (a before b would only apply if both present; here fields has only 'b')
    assert.ok(s1.includes('"fields":{"b":"5"}'));
  } finally { await cleanup(); }
});

test('fingerprint is sensitive to action target/method change (amend §8.1 #19)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    // content-sensitive fake hash: ${t} coerces the Uint8Array to its byte sequence,
    // so any input change (length OR content) yields a different fake fingerprint.
    const fakeHash = async (t) => `h(${t})`;
    const f = form({ action: 'https://x.edu.cn/search', method: 'get' });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { q: 'a' } };
    const fpGet = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    const fpPost = await mod.sha256Hex(mod.canonicalizeActionSnapshot({ ...f, method: 'post' }, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    assert.notEqual(fpGet, fpPost, 'method change → different fingerprint');
  } finally { await cleanup(); }
});

test('fingerprint is sensitive to a control value change (amend §8.1 #19)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fakeHash = async (t) => `h(${t})`;
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: {} };
    const f1 = form({ elements: [elt('page', 'text', '2')] });
    const f2 = form({ elements: [elt('page', 'text', '3')] });
    const a = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f1, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    const b = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f2, action, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    assert.notEqual(a, b, 'control value change → different fingerprint');
  } finally { await cleanup(); }
});

test('fingerprint is sensitive to FetchAction.fields change (amend §8.1 #19)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fakeHash = async (t) => `h(${t})`;
    const f = form({ elements: [elt('page', 'text', '1')] });
    const a = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f, { kind: 'form_submit', form_name: 'pageForm', fields: { page: '2' } }, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    const b = await mod.sha256Hex(mod.canonicalizeActionSnapshot(f, { kind: 'form_submit', form_name: 'pageForm', fields: { page: '9' } }, { location: { href: 'https://x.edu.cn/list' } }), fakeHash);
    assert.notEqual(a, b, 'fields change → different fingerprint');
  } finally { await cleanup(); }
});

test('virtual apply: FetchAction.fields override control value; disabled/unchecked ignored (amend §5.1)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const f = form({ elements: [
      elt('page', 'text', '1'),
      elt('cb', 'checkbox', 'on', { checked: false }),   // unchecked → ignored
      elt('cb2', 'checkbox', 'on', { checked: true }),
      elt('dis', 'text', 'x', { disabled: true }),        // disabled → ignored
    ] });
    const action = { kind: 'form_submit', form_name: 'pageForm', fields: { page: '5' } };
    const snap = mod.canonicalizeActionSnapshot(f, action, { location: { href: 'https://x.edu.cn/list' } });
    // page overridden to '5'; cb ignored; cb2 kept; dis ignored
    assert.ok(snap.includes('"page","text","5"'), 'field override applied');
    assert.ok(!snap.includes('"dis"'), 'disabled ignored');
    assert.ok(snap.includes('"cb2"'), 'checked checkbox kept');
    assert.ok(!snap.includes('"cb"'), 'unchecked checkbox ignored');
  } finally { await cleanup(); }
});

test('sha256Hex default uses crypto.subtle (real digest, 64 hex chars)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/fingerprint.ts', 'fingerprint.ts');
  try {
    const fp = await mod.sha256Hex('hello');
    assert.equal(fp.length, 64);
    assert.match(fp, /^[0-9a-f]{64}$/);
    // known SHA-256 of "hello"
    assert.equal(fp, '2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824');
  } finally { await cleanup(); }
});
