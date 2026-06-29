import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

// A minimal fake DOM node: holds children by selector, supports remove + cloneNode.
function makeNode(tagName = 'div', attrs = {}, children = []) {
  const node = {
    tagName,
    _attrs: { ...attrs },
    _children: [...children],
    _removed: false,
    getAttribute(n) { return n in this._attrs ? this._attrs[n] : null; },
    remove() { this._removed = true; },
    cloneNode(deep) {
      const cloneChild = (k) => (typeof k === 'string' ? k : k.cloneNode(true));
      const c = makeNode(this.tagName, { ...this._attrs }, deep ? this._children.map(cloneChild) : []);
      return c;
    },
    querySelectorAll(sel) {
      const out = [];
      const walk = (n) => {
        for (const ch of n._children) {
          if (typeof ch !== 'object' || ch._removed) continue;
          if (matches(ch, sel)) out.push(ch);
          out.push(...ch.querySelectorAll(sel));
        }
      };
      walk(this);
      return out;
    },
    get outerHTML() {
      const a = Object.entries(this._attrs).map(([k, v]) => ` ${k}="${v}"`).join('');
      const inner = this._children
        .filter((c) => typeof c !== 'object' || !c._removed)
        .map((c) => (typeof c === 'object' ? c.outerHTML : String(c)))
        .join('');
      return `<${this.tagName}${a}>${inner}</${this.tagName}>`;
    },
  };
  return node;
}
function matches(node, sel) {
  // support only the simple selectors used by stripCaptureNoise: `#id`, `tag`, `[attr]`, `tag[attr]`, comma lists
  for (const part of sel.split(',')) {
    const p = part.trim();
    let ok = true;
    const idM = p.match(/^#([\w-]+)$/);
    const tagM = p.match(/^([a-z*]+)(\[([\w-]+)\])?$/);
    const attrM = p.match(/^\[([\w-]+)\]$/);
    if (idM) ok = ok && node._attrs.id === idM[1];
    else if (attrM) ok = ok && (attrM[1] in node._attrs);
    else if (tagM) ok = ok && (tagM[1] === '*' || node.tagName === tagM[1]) && (!tagM[3] || (tagM[3] in node._attrs));
    else ok = false;
    if (ok) return true;
  }
  return false;
}

test('stripCaptureNoise removes ycl-panel, svg, style, canvas', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [
      makeNode('div', { id: 'ycl-panel' }),
      makeNode('svg'),
      makeNode('style'),
      makeNode('canvas'),
      makeNode('p', {}, ['keep']),
    ]);
    mod.stripCaptureNoise(root, {});
    const removed = root.querySelectorAll('#ycl-panel,svg,style,canvas');
    assert.equal(removed.length, 0, 'noise nodes removed');
    assert.equal(root._children.filter((c) => !c._removed).length, 1, 'only the <p> survives');
  } finally { await cleanup(); }
});

test('stripCaptureNoise removes a <footer role=contentinfo>', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [makeNode('footer', { role: 'contentinfo' })]);
    mod.stripCaptureNoise(root, {});
    assert.equal(root._children[0]._removed, true);
  } finally { await cleanup(); }
});

test('stripCaptureNoise does NOT strip header when stripHeader omitted (byte-aligned default)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [makeNode('header', { class: 'site-header' })]);
    mod.stripCaptureNoise(root, {});
    assert.equal(root._children[0]._removed, false, 'header kept by default');
  } finally { await cleanup(); }
});

test('stripCaptureNoise strips header when stripHeader: true', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('div', {}, [makeNode('header', { class: 'site-header' })]);
    mod.stripCaptureNoise(root, { stripHeader: true });
    assert.equal(root._children[0]._removed, true);
  } finally { await cleanup(); }
});

test('shouldStripElementDescriptor: only first class segment identifies (NEU wrapper header regression)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    // class="wrapper header" → first segment "wrapper" is not a header name → NOT stripped
    assert.equal(mod.shouldStripElementDescriptor('header', { tagName: 'div', className: 'wrapper header', role: null, id: null }), false);
    // class="header" → stripped
    assert.equal(mod.shouldStripElementDescriptor('header', { tagName: 'div', className: 'header', role: null, id: null }), true);
  } finally { await cleanup(); }
});

test('serializeWithoutOverlay clones, strips, returns outerHTML', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    const root = makeNode('html', {}, [makeNode('body', {}, [makeNode('svg'), makeNode('p', {}, ['hi'])])]);
    const html = mod.serializeWithoutOverlay(root, { stripHeader: false });
    assert.ok(html.includes('<p>hi</p>'), 'content kept');
    assert.ok(!html.includes('<svg>'), 'svg stripped from clone (original untouched)');
  } finally { await cleanup(); }
});

test('isCaptureStable: true only when signature equal AND rounds met', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/capture.ts', 'capture.ts');
  try {
    assert.equal(mod.isCaptureStable('a', 'a', 3, 3), true);
    assert.equal(mod.isCaptureStable('a', 'a', 2, 3), false);
    assert.equal(mod.isCaptureStable('a', 'b', 3, 3), false);
  } finally { await cleanup(); }
});
