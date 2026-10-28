import express from 'express';
import path from 'node:path';
import { readFile } from 'node:fs/promises';
import { ZodError } from 'zod';
import { Database, ApiError } from './db';
import { collectionName, createCollection, writeRequest, searchRequest } from '../shared/contracts';
import { MODEL } from '../shared/model';

const app = express();
app.disable('x-powered-by');
const port = Number(process.env.PORT ?? 3000);
// Local tool: bind loopback, reject cross-origin mutations and DNS rebinding.
app.use((req, res, next) => {
  const host = req.hostname;
  if (!['127.0.0.1', 'localhost', '[::1]'].includes(host)) return res.status(403).json({ error: '仅允许本机访问' });
  const origin = req.get('origin');
  if (origin && ![`http://localhost:${port}`, `http://127.0.0.1:${port}`].includes(origin)) return res.status(403).json({ error: '请求来源不受信任' });
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Cross-Origin-Embedder-Policy', 'require-corp');
  next();
});
app.use(express.json({ limit: '8mb' }));
// Serve runtime modules before Vite: ORT's dynamic ?import URLs must stay raw.
app.use('/ort', express.static(path.resolve('public/ort')));
app.use('/models', express.static(path.resolve('public/models')));
const db = new Database();
let dbReady = false;
let dbError = '';
async function initializeDatabase() {
  try { await db.init(); dbReady = true; dbError = ''; console.log(`seekdb ready: ${db.version}`); }
  catch (error) {
    await db.close().catch(() => undefined);
    dbError = error instanceof Error ? error.message : String(error);
    console.error('seekdb startup failed:', dbError);
    if (db.mode === 'server') setTimeout(() => { void initializeDatabase(); }, 5000).unref();
  }
}
void initializeDatabase();

app.get('/api/health', (_req, res) => { res.json({ ready: dbReady, database: 'seekdb', mode: db.mode, version: db.version, error: dbError, model: MODEL }); });
app.get('/api/model-manifest', async (_req, res) => {
  try { res.json(JSON.parse(await readFile('public/models/manifest.json', 'utf8'))); }
  catch { res.status(503).json({ error: '模型尚未下载，请在 stepfun 目录执行 npm run models:download' }); }
});
app.use('/api', (_req, _res, next) => { if (!dbReady) throw new ApiError(503, dbError || 'seekdb 正在启动，首次运行可能需要下载数据库组件'); next(); });
app.get('/api/collections', async (_req, res) => { res.json(await db.run(() => db.list())); });
app.post('/api/collections', async (req, res) => {
  const input = createCollection.parse(req.body);
  res.status(201).json(await db.run(() => db.create(input.title, input.precision), false));
});
app.post('/api/collections/:name/chunks', async (req, res) => {
  const name = collectionName.parse(req.params.name);
  const input = writeRequest.parse(req.body);
  res.json(await db.run(() => db.write(name, input.space, input.chunks)));
});
app.post('/api/collections/:name/refresh', async (req, res) => {
  const name = collectionName.parse(req.params.name);
  res.json(await db.run(() => db.refresh(name)));
});
app.post('/api/collections/:name/search', async (req, res) => {
  const name = collectionName.parse(req.params.name);
  const input = searchRequest.parse(req.body);
  res.json(await db.run(() => db.search(name, input)));
});
app.use('/api', (_req, res) => { res.status(404).json({ error: 'API 不存在' }); });
app.use((error: Error, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
  const status = error instanceof ZodError ? 400 : error instanceof ApiError ? error.status : 500;
  res.status(status).json({ error: error instanceof ZodError ? error.issues.map(i => `${i.path.join('.')}: ${i.message}`).join('; ') : error.message });
});
if (process.argv.includes('--dev')) {
  const { createServer } = await import('vite');
  const vite = await createServer({ server: { middlewareMode: true }, appType: 'spa' });
  app.use(vite.middlewares);
} else {
  app.use(express.static(path.resolve('dist')));
  app.use((_req, res) => { res.sendFile(path.resolve('dist/index.html')); });
}
const server = app.listen(port, '127.0.0.1', () => { console.log(`Local knowledge: http://127.0.0.1:${port}`); });
async function stop() { server.close(); await db.close(); process.exit(0); }
process.on('SIGINT', stop);
process.on('SIGTERM', stop);
