import test from 'node:test';
import assert from 'node:assert/strict';
import { writeFile, mkdir } from 'node:fs/promises';
import { createApp } from '../src/server.js';
import { PowerContextClient } from '../src/powercontext.js';

// A real PowerContext service is required. No mocked server is used in this suite.
test('real PowerContext: durable task, Handoff, recall, concurrency and deletion', { timeout: 120000 }, async t => {
  const baseUrl = process.env.POWERCONTEXT_URL;
  assert.ok(baseUrl, 'Set POWERCONTEXT_URL to a real test PowerContext service.');
  const auth = process.env.POWERCONTEXT_AUTH_HEADER || (process.env.POWERCONTEXT_TOKEN ? `Bearer ${process.env.POWERCONTEXT_TOKEN}` : '');
  const headers = { 'Content-Type': 'application/json', ...(auth ? { Authorization: auth } : {}) };
  const scopeResponse = await fetch(`${baseUrl}/v1/scopes`, { method: 'POST', headers,
    body: JSON.stringify({ title: 'Task tracker integration test', summary: 'Disposable integration test records', idempotency_key: `task-tracker-test-${crypto.randomUUID()}` }) });
  assert.equal(scopeResponse.status, 201, `Scope creation failed: ${await scopeResponse.clone().text()}`);
  const scope = await scopeResponse.json();
  const env = { POWERCONTEXT_URL: baseUrl, POWERCONTEXT_SCOPE_ID: scope.scope_id, POWERCONTEXT_AUTH_HEADER: auth };
  let server;
  const start = async () => {
    server = createApp(env).listen(0, '127.0.0.1');
    await new Promise(resolve => server.once('listening', resolve));
    return `http://127.0.0.1:${server.address().port}`;
  };
  const close = () => new Promise(resolve => server.close(resolve));
  t.after(async () => { if (server?.listening) await close(); });
  let origin = await start();
  const call = async (path, body, method = body === undefined ? 'GET' : 'POST') => {
    const response = await fetch(origin + '/api' + path, { method, headers: { 'Content-Type': 'application/json' }, body: body === undefined ? undefined : JSON.stringify(body) });
    return { status: response.status, data: await response.json() };
  };
  assert.equal((await call('/health')).status, 200);
  const created = await call('/tasks', { title: 'OceanReview checkpoint', description: 'review deployment', priority: 'high' });
  assert.equal(created.status, 201, JSON.stringify(created));
  const id = created.data.id;
  const updated = await call(`/tasks/${id}`, { revision: created.data.revision, progress: 60 }, 'PUT');
  assert.equal(updated.status, 200, JSON.stringify(updated));
  assert.equal(updated.data.status, 'in_progress');
  assert.equal((await call(`/tasks/${id}`, { revision: created.data.revision, progress: 20 }, 'PUT')).status, 409);
  const handoff = await call('/handoff', { taskId: id, summary: 'OceanReview deployed and reviewed', nextAction: 'Continue deployment verification' });
  assert.equal(handoff.status, 201, JSON.stringify(handoff));
  await close(); origin = await start();
  const restored = await call('/tasks');
  assert.equal(restored.data.tasks[0].id, id);
  assert.equal(restored.data.tasks[0].progress, 60);
  const restoredHandoff = await call('/handoff');
  assert.equal(restoredHandoff.data.handoffs[0].nextAction, 'Continue deployment verification');
  const context = await call('/context', { query: 'OceanReview' });
  assert.equal(context.status, 200, JSON.stringify(context));
  assert.equal(context.data.status, 'ready', JSON.stringify(context));
  assert.match(context.data.content, /OceanReview/);
  // Fresh hosted Function instance also reads the same durable records.
  globalThis.Deno = { env: { get: name => env[name] } };
  const { handler: hosted } = await import('../functions/handler.js');
  delete globalThis.Deno;
  const hostedResponse = await hosted(new Request('https://site/functions/v1/app/tasks'));
  assert.equal((await hostedResponse.json()).tasks[0].id, id);
  // Complete -> reopen -> zero must keep progress and state consistent.
  const completed = await call(`/tasks/${id}`, { revision: updated.data.revision, progress: 100 }, 'PUT');
  const reopened = await call(`/tasks/${id}`, { revision: completed.data.revision, progress: 20 }, 'PUT');
  assert.equal(reopened.data.status, 'in_progress');
  const reset = await call(`/tasks/${id}`, { revision: reopened.data.revision, progress: 0 }, 'PUT');
  assert.equal(reset.data.status, 'todo');
  const client = new PowerContextClient({ baseUrl, scopeId: scope.scope_id, authHeader: auth });
  const read = await client.get('task-tracker.task.v1', id);
  // An opaque ETag from an earlier read must be rejected after another write.
  const next = await call(`/tasks/${id}`, { revision: reset.data.revision, progress: 30 }, 'PUT');
  await assert.rejects(client.replace('task-tracker.task.v1', { ...read.record, progress: 90 }, read.etag), error => error.status === 409);
  assert.equal((await call(`/tasks/${id}`, { revision: next.data.revision }, 'DELETE')).status, 200);
  assert.equal((await call('/tasks')).data.tasks.length, 0);
  assert.equal((await call('/handoff')).data.handoffs.length, 1);
  assert.equal((await fetch(origin + '/app.js')).headers.get('content-type').includes('javascript'), true);
  assert.equal((await fetch(origin + '/missing.js')).status, 404);
  await mkdir('test-results', { recursive: true });
  await writeFile('test-results/integration.json', JSON.stringify({ testedAt: new Date().toISOString(), scopeId: scope.scope_id,
    taskId: id, handoffId: handoff.data.id, restoredProgress: 60, contextStatus: context.data.status,
    staleRevisionRejected: true, staleEtagRejected: true, applicationRestartRestored: true }, null, 2));
});
