import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from '../harness.mjs';

function input(o = {}) {
  return { title: '', bodyText: '', readyState: 'complete', linksLength: 0, bodyNull: false, ...o };
}

test('errorPage: short body + 502 keyword → errorPage true (byte-aligned with userscript)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    assert.equal(mod.detectPage(input({ title: '502 Bad Gateway', bodyText: 'x'.repeat(200) })).errorPage, true);
    assert.equal(mod.detectPage(input({ title: '503 Service', bodyText: 'server error' })).errorPage, true);
  } finally { await cleanup(); }
});

test('errorPage: long body (>1500) is NOT an error page', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    assert.equal(mod.detectPage(input({ title: '502 Bad Gateway', bodyText: 'x'.repeat(2000) })).errorPage, false);
  } finally { await cleanup(); }
});

test('terminalReason not_found: short body + 404 keyword', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ title: '页面不存在', bodyText: '您访问的页面不存在' }));
    assert.equal(d.terminalReason, 'not_found');
  } finally { await cleanup(); }
});

test('terminalReason content_removed: 已下线 keyword', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ bodyText: '该内容已下线' }));
    assert.equal(d.terminalReason, 'content_removed');
  } finally { await cleanup(); }
});

test('terminalReason empty_page: complete + empty body + no links', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ readyState: 'complete', bodyText: '   ', linksLength: 0 }));
    assert.equal(d.terminalReason, 'empty_page');
    // body present but null (no <body>) is NOT empty_page (guard mirrors userscript: document.body !== null)
    assert.equal(mod.detectPage(input({ bodyNull: true, bodyText: '', linksLength: 0 })).terminalReason, null);
  } finally { await cleanup(); }
});

test('terminalReason null on a normal long page', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    const d = mod.detectPage(input({ title: '教师列表', bodyText: '张三 李四 王五 '.repeat(500), linksLength: 40 }));
    assert.equal(d.terminalReason, null);
    assert.equal(d.errorPage, false);
  } finally { await cleanup(); }
});

test('terminalReason precedence: not_found before empty_page (matches userscript order)', async () => {
  const { mod, cleanup } = await importTsModule('../src/content/pageDetect.ts', 'pageDetect.ts');
  try {
    // a not_found-pattern page that also has an empty body: userscript checks NOT_FOUND first
    const d = mod.detectPage(input({ title: '404', bodyText: 'Not Found' }));
    assert.equal(d.terminalReason, 'not_found');
  } finally { await cleanup(); }
});
