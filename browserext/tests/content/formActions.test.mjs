import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// Byte-aligned synthetic_url regression trap: must match userscript buildSyntheticUrl exactly.
test('buildSyntheticUrl byte-matches userscript (__ycl_* params; hash cleared; __ycl_ stripped first)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const u = mod.buildSyntheticUrl('https://xjtu.edu.cn/list?__ycl_kind=stale&page=2#frag', 'pageForm', 'PAGENUM', 3);
    assert.equal(u, 'https://xjtu.edu.cn/list?page=2&__ycl_kind=form&__ycl_form=pageForm&__ycl_field=PAGENUM&__ycl_page=3');
  } finally { await cleanup(); }
});

test('buildSyntheticUrl drops explicit-port URLs (returns input unchanged shape)', async () => {
  // The userscript collectFormPaginationStates early-returns [] on explicit-port URLs;
  // buildSyntheticUrl itself still constructs via URL (which keeps the port). We assert
  // the __ycl_* params are present and the port is preserved (mirrors userscript).
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const u = mod.buildSyntheticUrl('http://x.edu.cn:8080/list', 'f', 'p', 1);
    assert.ok(u.includes('__ycl_kind=form'));
    assert.ok(u.includes(':8080'));
  } finally { await cleanup(); }
});

// collectFormPaginationStates: drive it with a fake document mirroring the userscript's reads.
function fakeDoc({ forms = [], anchors = [], inputs = [], currentPageText = '0', locationHref = 'https://xjtu.edu.cn/list' } = {}) {
  return {
    forms: makeFormsCollection(forms),
    querySelectorAll(sel) {
      if (sel === 'a[href^="javascript:"]') return anchors;
      if (sel === 'input[name]') return inputs;
      if (sel === '*') return [];
      return [];
    },
    querySelector(sel) {
      if (sel === '.this-page') return currentPageText ? { textContent: currentPageText } : null;
      return null;
    },
    links: { length: 10 },
    location: { href: locationHref },
  };
}
function makeFormsCollection(forms) {
  // document.forms supports namedItem + numeric index. We model it as a plain
  // object (the source only uses .namedItem/.length/[index], never calls it as
  // a function); wrapping a function would clash with its non-writable .length
  // under ESM strict mode.
  const col = {
    namedItem(name) { return forms.find((f) => f.name === name) ?? null; },
    length: forms.length,
  };
  for (let i = 0; i < forms.length; i++) col[i] = forms[i];
  return col;
}
function makeForm(name, { action = '', method = 'get', elements = [], submit = () => {}, id = null, enctype = '', target = '' } = {}) {
  const elts = makeElementsCollection(elements);
  // _index is assigned by the caller via forms[i]; here we default to 0 for the smoke tests.
  return { name, action, method, elements: elts, _submit: submit, submit() { this._submit(); }, _index: 0, id, enctype, target };
}
function makeElementsCollection(elements) {
  const col = {
    namedItem(name) { return elements.find((e) => e.name === name) ?? null; },
    length: elements.length,
  };
  for (let i = 0; i < elements.length; i++) col[i] = elements[i];
  return col;
}
function makeInput(name, { type = 'text', value = '', form = null, checked = true } = {}) {
  const input = { name, type, value, form, _value: value };
  Object.defineProperty(input, 'value', { get() { return this._value; }, set(v) { this._value = v; }, configurable: true });
  return input;
}

test('collectFormPaginationStates builds one state per javascript: page-assignment anchor', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const anchors = [
      { getAttribute: () => 'javascript:document.forms[\'pageForm\'].PAGENUM.value=2' },
      { getAttribute: () => 'javascript:document.forms[\'pageForm\'].PAGENUM.value=3' },
    ];
    const doc = fakeDoc({ forms: [makeForm('pageForm')], anchors, inputs: [] });
    const states = mod.collectFormPaginationStates('https://xjtu.edu.cn/list', doc);
    assert.equal(states.length, 2);
    assert.equal(states[0].page_index, 2);
    assert.equal(states[0].form_name, 'pageForm');
    assert.equal(states[0].fields.PAGENUM, '2');
    assert.equal(states[0].submit, true);
    assert.equal(states[0].synthetic_url, 'https://xjtu.edu.cn/list?__ycl_kind=form&__ycl_form=pageForm&__ycl_field=PAGENUM&__ycl_page=2');
    assert.equal(states[1].synthetic_url, 'https://xjtu.edu.cn/list?__ycl_kind=form&__ycl_form=pageForm&__ycl_field=PAGENUM&__ycl_page=3');
  } finally { await cleanup(); }
});

test('collectFormPaginationStates expands GOPAGE field to 1..max when a *GOPAGE input exists', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const anchors = [{ getAttribute: () => 'javascript:document.forms[\'pageForm\'].currentGOPAGE.value=3' }];
    const form = makeForm('pageForm');
    const doc = fakeDoc({ forms: [form], anchors, inputs: [makeInput('currentGOPAGE', { form })] });
    const states = mod.collectFormPaginationStates('https://xjtu.edu.cn/list', doc);
    // expandPageIndexes returns 1..3; pageIndex<=1 skipped → states for page 2 and 3
    const pages = states.map((s) => s.page_index).sort((a, b) => a - b);
    assert.deepEqual(pages, [2, 3]);
  } finally { await cleanup(); }
});

test('performFetchAction sets control values and submits when submit !== false', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    let submitted = false;
    const fld = makeInput('PAGENUM', { value: '1' });
    const form = makeForm('pageForm', { elements: [fld], submit: () => { submitted = true; } });
    const doc = fakeDoc({ forms: [form] });
    const ok = mod.performFetchAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '5' }, submit: true }, doc);
    assert.equal(ok, true);
    assert.equal(fld.value, '5');
    assert.equal(submitted, true);
  } finally { await cleanup(); }
});

test('performFetchAction returns false when the form is missing', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const doc = fakeDoc({ forms: [] });
    assert.equal(mod.performFetchAction({ kind: 'form_submit', form_name: 'nope', fields: {}, submit: true }, doc), false);
  } finally { await cleanup(); }
});

test('performFetchAction does NOT submit when action.submit === false', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    let submitted = false;
    const fld = makeInput('PAGENUM', { value: '1' });
    const form = makeForm('pageForm', { elements: [fld], submit: () => { submitted = true; } });
    const doc = fakeDoc({ forms: [form] });
    mod.performFetchAction({ kind: 'form_submit', form_name: 'pageForm', fields: { PAGENUM: '5' }, submit: false }, doc);
    assert.equal(submitted, false);
    assert.equal(fld.value, '5', 'control still set');
  } finally { await cleanup(); }
});

// --- prepareFormAction smoke tests (amend §5.1; minimal) ---

test('prepareFormAction ok:true on a valid GET form (fingerprint computed)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const fld = makeInput('q', { value: 'init' });
    const form = makeForm('searchForm', { action: '/search', method: 'get', elements: [fld] });
    const doc = fakeDoc({ forms: [form], locationHref: 'https://x.edu.cn/list' });
    const r = await mod.prepareFormAction({ kind: 'form_submit', form_name: 'searchForm', fields: { q: 'cat' }, submit: true }, doc);
    assert.equal(r.ok, true);
    assert.equal(r.method, 'GET');
    assert.equal(r.expectedEffect, 'same_document');
    assert.match(r.preparationFingerprint, /^[0-9a-f]{64}$/, 'real sha256 hex');
    assert.ok(r.targetUrl.startsWith('https://x.edu.cn/search'), 'targetUrl resolved against base');
    assert.ok(r.targetUrl.includes('q=cat'), 'field override merged into GET query');
  } finally { await cleanup(); }
});

test('prepareFormAction ok:false form_not_found when the form is missing', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const doc = fakeDoc({ forms: [], locationHref: 'https://x.edu.cn/list' });
    const r = await mod.prepareFormAction({ kind: 'form_submit', form_name: 'nope', fields: {}, submit: true }, doc);
    assert.equal(r.ok, false);
    assert.equal(r.error, 'form_not_found');
  } finally { await cleanup(); }
});

test('prepareFormAction ok:false offsite_redirect when form.action targets a non-edu host', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    const form = makeForm('f', { action: 'https://evil.com/steal', method: 'post' });
    const doc = fakeDoc({ forms: [form], locationHref: 'https://x.edu.cn/list' });
    const r = await mod.prepareFormAction({ kind: 'form_submit', form_name: 'f', fields: {}, submit: true }, doc);
    assert.equal(r.ok, false);
    assert.equal(r.error, 'offsite_redirect');
  } finally { await cleanup(); }
});

test('prepareFormAction is side-effect-free (no control mutation, no submit)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/formActions.ts', 'formActions.ts');
  try {
    let submitted = false;
    const fld = makeInput('q', { value: 'init' });
    const form = makeForm('searchForm', { action: '/search', method: 'get', elements: [fld], submit: () => { submitted = true; } });
    const doc = fakeDoc({ forms: [form], locationHref: 'https://x.edu.cn/list' });
    await mod.prepareFormAction({ kind: 'form_submit', form_name: 'searchForm', fields: { q: 'cat' }, submit: true }, doc);
    assert.equal(submitted, false, 'no submit called');
    assert.equal(fld.value, 'init', 'control value untouched');
  } finally { await cleanup(); }
});
