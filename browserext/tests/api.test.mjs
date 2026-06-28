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
