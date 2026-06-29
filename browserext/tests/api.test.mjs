import assert from 'node:assert/strict';
import test from 'node:test';
import { importTsModule } from './harness.mjs';

function fakeFetch(routes) {
  // routes: { 'GET /api/status': {status, body}, 'POST /jobs/:id/fail': ..., ... }
  return async (url, init) => {
    const u = new URL(url);
    const method = (init?.method || 'GET').toUpperCase();
    let key = `${method} ${u.pathname}`;
    // match /jobs/{id}/fail etc.
    const jobMatch = u.pathname.match(/^\/api\/jobs\/([^/]+)\/(fail|skip)$/);
    if (jobMatch) key = `POST /jobs/:id/${jobMatch[2]}`;
    const route = routes[key];
    if (!route) throw new Error(`no fake route for ${key}`);
    return {
      status: route.status,
      ok: route.status >= 200 && route.status < 300,
      json: async () => route.body,
      text: async () => JSON.stringify(route.body ?? ''),
    };
  };
}

test('getStatus parses current_job and frontend_health', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = fakeFetch({
      'GET /api/status': { status: 200, body: {
        queue: { pending: 0, assigned: 1, completed: 2, failed: 0, skipped: 1 },
        current_job: { id: 'abc', url: 'https://x.edu.cn/p' },
        frontend_health: { alive: true, last_seen_seconds_ago: 1.2 },
      } },
    });
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s.current_job.id, 'abc');
    assert.equal(s.current_job.url, 'https://x.edu.cn/p');
    assert.equal(s.frontend_health.alive, true);
    assert.equal(s.frontend_health.last_seen_seconds_ago, 1.2);
  } finally {
    await cleanup();
  }
});

test('getStatus returns null current_job when none in flight', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = fakeFetch({
      'GET /api/status': { status: 200, body: {
        queue: { pending: 0, assigned: 0, completed: 0, failed: 0, skipped: 0 },
        current_job: null,
        frontend_health: { alive: false, last_seen_seconds_ago: null },
      } },
    });
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s.current_job, null);
    assert.equal(s.frontend_health.alive, false);
  } finally {
    await cleanup();
  }
});

test('getStatus returns null on fetch error (backend down)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNREFUSED'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s, null);
  } finally {
    await cleanup();
  }
});

test('failJob POSTs message', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let capturedBody = null;
    const fetchFn = async (url, init) => {
      capturedBody = JSON.parse(init.body);
      return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.failJob('abc', 'gateway_5xx');
    assert.deepEqual(capturedBody, { message: 'gateway_5xx' });
  } finally {
    await cleanup();
  }
});

test('skipJob POSTs reason', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let capturedBody = null;
    const fetchFn = async (url, init) => {
      capturedBody = JSON.parse(init.body);
      return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.skipJob('abc', 'not_found');
    assert.deepEqual(capturedBody, { reason: 'not_found' });
  } finally {
    await cleanup();
  }
});

test('failJob and skipJob swallow errors (idempotent; backend already-resolved → no-op)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('network'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    // must NOT throw — late/stale reports are normal (backend 60s timeout may have fired)
    await api.failJob('abc', 'gateway_5xx');
    await api.skipJob('abc', 'not_found');
  } finally {
    await cleanup();
  }
});

test('sendHeartbeat POSTs the heartbeat payload', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let captured = null;
    const fetchFn = async (url, init) => {
      captured = { url, method: init.method, body: JSON.parse(init.body) };
      return { status: 200, ok: true, json: async () => ({ status: 'ok' }) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.sendHeartbeat({
      owner_tab_id: 'dext-ext-7-1000',
      url: 'https://x.edu.cn/p',
      current_job_id: 'job-1',
      auto_mode: true,
      paused: false,
      timestamp: 12345,
    });
    assert.equal(captured.url, 'http://127.0.0.1:21520/api/heartbeat');
    assert.equal(captured.method, 'POST');
    assert.deepEqual(captured.body, {
      owner_tab_id: 'dext-ext-7-1000',
      url: 'https://x.edu.cn/p',
      current_job_id: 'job-1',
      auto_mode: true,
      paused: false,
      timestamp: 12345,
    });
  } finally {
    await cleanup();
  }
});

test('sendHeartbeat swallows errors (backend down is reflected by /status, not heartbeat)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNREFUSED'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.sendHeartbeat({
      owner_tab_id: 'dext-ext-7-1000', url: '', current_job_id: null,
      auto_mode: false, paused: false, timestamp: 1,
    });
  } finally {
    await cleanup();
  }
});

test('getStatus returns the full FetchJob shape for current_job (widened type)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fullJob = {
      id: 'abc', url: 'https://x.edu.cn/p', status: 'assigned',
      context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
      created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
    };
    const fetchFn = fakeFetch({
      'GET /api/status': { status: 200, body: {
        queue: { pending: 0, assigned: 1, completed: 0, failed: 0, skipped: 0 },
        current_job: fullJob,
        frontend_health: { alive: true, last_seen_seconds_ago: 1 },
      } },
    });
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    const s = await api.getStatus();
    assert.equal(s.current_job.id, 'abc');
    assert.equal(s.current_job.url, 'https://x.edu.cn/p');
    assert.equal(s.current_job.status, 'assigned');
    assert.equal(s.current_job.timeout_seconds, 60);
  } finally {
    await cleanup();
  }
});

test('claimNextJob GETs /jobs/next; 204 → null; 200 → FetchJob', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fullJob = {
      id: 'abc', url: 'https://x.edu.cn/p', status: 'assigned',
      context: { university_name: 'X', agent_state: '', intent: '', parent_url: '', depth: 0, org_unit_name: '', hints: [] },
      created_at: '2026-06-28T00:00:00', timeout_seconds: 60, action: null, identity_url: null,
    };
    let count = 0;
    const fetchFn = async () => {
      count += 1;
      if (count === 1) return { status: 204, ok: true, json: async () => null };
      return { status: 200, ok: true, json: async () => fullJob };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    assert.equal(await api.claimNextJob(), null);
    const j = await api.claimNextJob();
    assert.equal(j.id, 'abc');
  } finally {
    await cleanup();
  }
});

test('claimNextJob returns null on fetch error', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNREFUSED'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    assert.equal(await api.claimNextJob(), null);
  } finally {
    await cleanup();
  }
});

test('completeJob POSTs /complete with html/url/title/pagination_states', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let posted = null;
    const fetchFn = async (url, init) => {
      posted = { url, init };
      return { status: 200, ok: true, json: async () => ({}) };
    };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await api.completeJob('job-1', '<html/>', 'https://x.edu.cn/p', 'Title', [{ kind: 'form_submit', state_id: 's', label: 'l', page_index: 2, form_name: 'f', fields: { p: '2' }, submit: true, synthetic_url: 'syn', url: 'u' }]);
    assert.equal(posted.url, 'http://127.0.0.1:21520/api/jobs/job-1/complete');
    assert.equal(posted.init.method, 'POST');
    const body = JSON.parse(posted.init.body);
    assert.equal(body.html, '<html/>');
    assert.equal(body.url, 'https://x.edu.cn/p');
    assert.equal(body.title, 'Title');
    assert.equal(body.pagination_states.length, 1);
  } finally {
    await cleanup();
  }
});

test('completeJob swallows errors (late /complete idempotency — CLAUDE.md bug trap)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('ECONNRESET'); };
    const api = mod.createFetchApi('http://127.0.0.1:21520/api', fetchFn);
    await assert.doesNotReject(async () => { await api.completeJob('job-1', 'x', 'u', 't'); });
  } finally {
    await cleanup();
  }
});

test('getDecision: 200 → PendingDecision; 204 → null', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    let status = 204, body = null;
    const fetchFn = async () => ({ status, ok: status < 400, json: async () => body });
    const api = mod.createFetchApi('http://x/api', fetchFn);
    assert.equal(await api.getDecision(), null);
    status = 200; body = { id: 'dec-1', kind: 'dedup', org_unit_name: 'X', failure_count: 1, sample_urls: [], suggested_action: 'skip', status: 'pending', action: null, created_at: 't' };
    const dec = await api.getDecision();
    assert.equal(dec?.id, 'dec-1');
  } finally { await cleanup(); }
});

test('getDecision: network error → null (swallow)', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const fetchFn = async () => { throw new Error('net'); };
    const api = mod.createFetchApi('http://x/api', fetchFn);
    assert.equal(await api.getDecision(), null);
  } finally { await cleanup(); }
});

test('resolveDecision: POSTs /decision/{id}/resolve with {action}; swallows errors', async () => {
  const { mod, cleanup } = await importTsModule('../src/api.ts', 'api.ts');
  try {
    const calls = [];
    const fetchFn = async (url, init) => { calls.push({ url, init }); return { ok: true }; };
    const api = mod.createFetchApi('http://x/api', fetchFn);
    await api.resolveDecision('dec-1', 'accept');
    assert.equal(calls[0].url, 'http://x/api/decision/dec-1/resolve');
    assert.equal(calls[0].init.method, 'POST');
    assert.deepEqual(JSON.parse(calls[0].init.body), { action: 'accept' });
    // error swallow
    const errApi = mod.createFetchApi('http://x/api', async () => { throw new Error('net'); });
    await errApi.resolveDecision('dec-1', 'accept');   // does not throw
  } finally { await cleanup(); }
});
