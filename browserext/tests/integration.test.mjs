import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeArea() {
  const store = new Map();
  return {
    async get(keys) { const arr = keys === null ? [...store.keys()] : (Array.isArray(keys) ? keys : [keys]); const o = {}; for (const k of arr) if (store.has(k)) o[k] = store.get(k); return o; },
    async set(o) { for (const [k, v] of Object.entries(o)) store.set(k, v); },
    async remove(keys) { const arr = Array.isArray(keys) ? keys : [keys]; for (const k of arr) store.delete(k); },
  };
}

function job(id, url = `https://xjtu.edu.cn/${id}`) {
  return { id, url, status: 'assigned', context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] }, created_at: 't', timeout_seconds: 60, action: null, identity_url: null };
}

test('integration: bind → claim → navigate → land → capture → complete (gate ON, slice-5 acceptance)', async () => {
  const ctrlBundle = await importTsModule('../src/controller/controller.ts', 'controller.ts');
  const routerBundle = await importTsModule('../src/controller/messageRouter.ts', 'messageRouter.ts');
  const ctrlMod = ctrlBundle.mod;
  const routerMod = routerBundle.mod;
  try {
    const area = fakeArea();
    // Fake api: first getStatus returns job-1; after complete, returns null.
    let completed = false;
    const api = {
      async getStatus() { return completed ? { current_job: null, frontend_health: { alive: true, last_seen_seconds_ago: 1 } } : { current_job: job('job-1'), frontend_health: { alive: true, last_seen_seconds_ago: 1 } }; },
      async claimNextJob() { return job('job-1'); },
      async failJob() {}, async skipJob() {}, async sendHeartbeat() {},
      async getDecision() { return null; }, async resolveDecision() {},
      async completeJob(id) { assert.equal(id, 'job-1'); completed = true; },
      async overrideJobUrl() { return null; },
    };
    const sent = [];
    const chrome = {
      async getTab() { return { id: 42, url: 'https://xjtu.edu.cn/job-1' }; },
      async updateTabUrl(tabId, url) { sent.push({ type: 'updateTabUrl', tabId, url }); },
      async sendMessage(tabId, message, options) { sent.push({ type: 'sendMessage', tabId, message, options }); return { received: true }; },
      onMessage() {},
    };
    const c = ctrlMod.createCrawlController({ storage: ctrlMod.createControllerStorage(area), api, chrome });
    const router = routerMod.createMessageRouter({ controller: c, chrome, api, extensionId: 'ext-123' });

    const sender = (documentId = 'DOC-1') => ({ id: 'ext-123', tab: { id: 42, url: 'https://xjtu.edu.cn/job-1' }, frameId: 0, documentId });

    // 1. bind (command from the tab that will become bound)
    await router.handle({ op: 'COMMAND', command: { kind: 'bind' } }, sender());
    // 2. enable auto + tick → claim + navigate
    await router.handle({ op: 'COMMAND', command: { kind: 'set_auto', value: true } }, sender());
    await c.tick(2000);
    assert.equal((await c.getState()).phase, 'navigating');
    assert.ok(sent.some((s) => s.type === 'updateTabUrl' && s.url === 'https://xjtu.edu.cn/job-1'));

    // 3. landing signals: beforeRequest → committed → http ok → PAGE_READY
    await c.deliverBeforeRequest({ tabId: 42, frameId: 0, type: 'main_frame', url: 'https://xjtu.edu.cn/job-1', requestId: 'REQ-1', timeStamp: 2100 });
    await c.deliverCommitted({ tabId: 42, frameId: 0, documentId: 'DOC-1', url: 'https://xjtu.edu.cn/job-1', timeStamp: 2200 });
    await c.deliverHttpEvent({ tabId: 42, url: 'https://xjtu.edu.cn/job-1', statusCode: 200, frameId: 0, requestId: 'REQ-1', documentId: 'DOC-1', timeStamp: 2300 }, 'ok');
    await router.handle({ op: 'PAGE_READY', url: 'https://xjtu.edu.cn/job-1', title: 'T', detection: { errorPage: false, terminalReason: null } }, sender('DOC-1'));
    assert.equal((await c.getState()).phase, 'capturing', 'landed → capturing (CAPTURE dispatched via sendMessage)');
    assert.ok(sent.some((s) => s.type === 'sendMessage' && s.message.op === 'CAPTURE' && s.options.documentId === 'DOC-1'));

    // 4. CAPTURE_RESULT ok → complete
    const rpc = (await c.getState()).pendingRpc;
    await router.handle({ op: 'CAPTURE_RESULT', rpcId: rpc.id, jobId: 'job-1', ok: true, url: 'https://xjtu.edu.cn/job-1', html: '<html/>', title: 'T', paginationStates: [], detection: { errorPage: false, terminalReason: null } }, sender('DOC-1'));
    assert.equal(completed, true, '/jobs/job-1/complete called');
    assert.equal((await c.getState()).phase, 'idle');

    // 5. STATE_CHANGED was broadcast to the bound tab after the command(s).
    assert.ok(sent.some((s) => s.type === 'sendMessage' && s.message.op === 'STATE_CHANGED'), 'STATE_CHANGED broadcast (amend §7)');
  } finally {
    await ctrlBundle.cleanup();
    await routerBundle.cleanup();
  }
});
