import express from 'express';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { createHandler, clientConfig } from './http.js';
export function createApp(env = process.env) {
  const app = express(), handler = createHandler(clientConfig(env));
  app.disable('x-powered-by');
  app.use(express.json({ limit: '16kb' }));
  app.use('/api', async (req, res) => {
    const response = await handler(new Request(`http://localhost${req.originalUrl}`, {
      method: req.method, headers: { 'Content-Type': 'application/json' },
      body: ['GET', 'HEAD'].includes(req.method) ? undefined : JSON.stringify(req.body) }));
    res.status(response.status);
    response.headers.forEach((value, name) => res.setHeader(name, value));
    res.send(await response.text());
  });
  const publicDir = fileURLToPath(new URL('../public/', import.meta.url));
  app.use(express.static(publicDir));
  for (const route of ['/tasks', '/handoff', '/about']) app.get(route, (_req, res) => res.sendFile(path.join(publicDir, 'index.html')));
  app.use((error, _req, res, _next) => res.status(error.status || 500).json({ error: '请求格式不正确。', code: 'invalid_request' }));
  return app;
}
if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  const port = Number(process.env.PORT || 3000), host = process.env.HOST || '127.0.0.1';
  createApp().listen(port, host, () => console.log(`Task Tracker: http://${host}:${port}`));
}
