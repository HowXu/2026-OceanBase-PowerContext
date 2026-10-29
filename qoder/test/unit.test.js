import test from 'node:test';
import assert from 'node:assert/strict';
import { PowerContextClient, AppError } from '../src/powercontext.js';
import { Tracker, TASK_KIND } from '../src/tracker.js';
import { createHandler } from '../src/http.js';

const req = (path, body, method = body === undefined ? 'GET' : 'POST') => new Request(`http://tracker/api${path}`, {
  method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });

test('unconfigured service never reports healthy or saved', async () => {
  const handler = createHandler({});
  for (const request of [req('/health'), req('/tasks'), req('/tasks', { title: 'test' })]) {
    const response = await handler(request);
    assert.equal(response.status, 503);
    assert.equal((await response.json()).code, 'not_configured');
  }
});
test('connection refusal is a failed save with no success response', async () => {
  const handler = createHandler({ baseUrl: 'http://127.0.0.1:1', scopeId: 'test-scope' });
  const response = await handler(req('/tasks', { title: 'failed save' }));
  assert.equal(response.status, 503);
  assert.equal((await response.json()).code, 'upstream_unavailable');
});
test('invalid form data and malformed JSON are rejected', async () => {
  const handler = createHandler({});
  for (const body of [{ title: ' ' }, { title: 'a', progress: 101 }, { title: 'a', progress: 1.5 }, { title: 'a', priority: 'urgent' }, { title: 'a', deadline: '2026-02-31' }]) {
    assert.equal((await handler(req('/tasks', body))).status, 400);
  }
  const malformed = new Request('http://tracker/api/tasks', { method: 'POST', body: '{' });
  assert.equal((await handler(malformed)).status, 400);
});
test('an untyped upstream health response cannot prove Memory availability', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ ok: true, status: 'healthy' }));
  const handler = createHandler({ baseUrl: 'https://pc.example', scopeId: 'scope' });
  assert.equal((await handler(req('/health'))).status, 502);
});
test('Memory pagination preserves service-returned IDs and server-selected Scope', async t => {
  const calls = [];
  t.mock.method(globalThis, 'fetch', async (_url, options) => {
    const body = JSON.parse(options.body); calls.push(body);
    return Response.json({ items: [{ artifact: { artifact_id: `memory-${calls.length}`, revision: 1 }, text: JSON.stringify({ schema: TASK_KIND, title: 'task', createdAt: '2026-01-01' }) }], next_cursor: calls.length === 1 ? 'opaque-cursor' : null });
  });
  const handler = createHandler({ baseUrl: 'https://pc.example', scopeId: 'trusted-scope' });
  const response = await handler(req('/tasks'));
  assert.equal(response.status, 200);
  assert.equal((await response.json()).tasks.length, 2);
  assert.equal(calls[1].cursor, 'opaque-cursor');
  assert.ok(calls.every(call => call.scope_id === 'trusted-scope'));
});
test('a repeated pagination cursor fails instead of silently truncating', async t => {
  t.mock.method(globalThis, 'fetch', async () => Response.json({ items: [], next_cursor: 'same' }));
  await assert.rejects(new PowerContextClient({ baseUrl: 'https://pc.example', scopeId: 'scope' }).list(TASK_KIND), error => error.code === 'invalid_pagination');
});
test('stale task revision cannot overwrite a newer session', async () => {
  const tracker = new Tracker({ get: async () => ({ record: { revision: 3 }, etag: 'opaque' }), replace: () => assert.fail('must not write') });
  await assert.rejects(tracker.updateTask('task', { revision: 2 }), error => error instanceof AppError && error.status === 409);
});
test('generated Qoder Function uses the same failure semantics', async () => {
  globalThis.Deno = { env: { get: () => undefined } };
  try {
    const { handler } = await import('../functions/handler.js');
    assert.equal((await handler(new Request('https://tracker/functions/v1/app/health'))).status, 503);
  } finally { delete globalThis.Deno; }
});
