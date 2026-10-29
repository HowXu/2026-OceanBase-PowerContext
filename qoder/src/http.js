import { AppError, PowerContextClient } from './powercontext.js';
import { Tracker, TASK_KIND } from './tracker.js';
export function clientConfig(env = {}) {
  return { baseUrl: env.POWERCONTEXT_URL || '', scopeId: env.POWERCONTEXT_SCOPE_ID || '',
    authHeader: env.POWERCONTEXT_AUTH_HEADER || (env.POWERCONTEXT_TOKEN ? `Bearer ${env.POWERCONTEXT_TOKEN}` : ''), timeoutMs: 10000 };
}
export function createHandler(config) {
  const client = new PowerContextClient(config), tracker = new Tracker(client);
  const json = (value, status = 200) => new Response(JSON.stringify(value), {
    status, headers: { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' } });
  return async request => {
    try {
      const pathname = new URL(request.url).pathname;
      if (!pathname.startsWith('/api/') && !pathname.startsWith('/functions/v1/app/')) return json({ error: '接口不存在。', code: 'not_found' }, 404);
      const route = pathname.startsWith('/functions/v1/app/') ? pathname.slice('/functions/v1/app'.length) : pathname.slice('/api'.length);
      const method = request.method;
      let body = {};
      if (!['GET', 'HEAD'].includes(method)) {
        const raw = await request.text();
        if (new TextEncoder().encode(raw).byteLength > 16384) throw new AppError(413, 'body_too_large', '请求内容过大。');
        try { body = raw ? JSON.parse(raw) : {}; } catch { throw new AppError(400, 'invalid_json', '请求必须为有效 JSON。'); }
        if (!body || typeof body !== 'object' || Array.isArray(body)) throw new AppError(400, 'invalid_json', '请求必须为 JSON 对象。');
      }
      if (['/health', '/powercontext/health'].includes(route) && method === 'GET') {
        await client.list(TASK_KIND);
        return json({ ok: true, status: 'connected', service: 'PowerContext', scopeId: client.scopeId });
      }
      if (route === '/tasks' && method === 'GET') return json({ tasks: await tracker.tasks() });
      if (route === '/tasks' && method === 'POST') return json(await tracker.createTask(body), 201);
      const match = route.match(/^\/tasks\/([^/]+)$/);
      if (match) {
        const id = decodeURIComponent(match[1]);
        if (method === 'GET') return json((await client.get(TASK_KIND, id)).record);
        if (method === 'PUT') return json(await tracker.updateTask(id, body));
        if (method === 'DELETE') { await tracker.updateTask(id, body, true); return json({ message: '任务已删除' }); }
      }
      if (route === '/summary' && method === 'GET') return json(await tracker.summary());
      if (route === '/handoff' && method === 'GET') return json({ handoffs: await tracker.handoffs() });
      if (route === '/handoff' && method === 'POST') return json(await tracker.createHandoff(body), 201);
      if (route === '/context' && method === 'POST') return json(await tracker.context(body));
      return json({ error: '接口不存在。', code: 'not_found' }, 404);
    } catch (error) {
      if (error instanceof AppError) return json({ error: error.message, code: error.code }, error.status);
      console.error('Task tracker internal error:', error instanceof Error ? error.name : 'UnknownError');
      return json({ error: '处理失败，请稍后重试。', code: 'internal_error' }, 500);
    }
  };
}
