import { readFile, writeFile } from 'node:fs/promises';
const baseUrl = (process.env.POWERCONTEXT_URL || '').replace(/\/+$/, '');
if (!baseUrl) throw new Error('先复制 .env.example 为 .env，并配置 POWERCONTEXT_URL。');
if (process.env.POWERCONTEXT_SCOPE_ID) throw new Error('已有 Scope ID。若要新建，请先确认并清空 .env 中的 POWERCONTEXT_SCOPE_ID。');
const headers = { 'Content-Type': 'application/json' };
const auth = process.env.POWERCONTEXT_AUTH_HEADER || (process.env.POWERCONTEXT_TOKEN ? `Bearer ${process.env.POWERCONTEXT_TOKEN}` : '');
if (auth) headers.Authorization = auth;
const response = await fetch(`${baseUrl}/v1/scopes`, { method: 'POST', headers, signal: AbortSignal.timeout(10000),
  body: JSON.stringify({ title: 'Task tracker', summary: '任务进度和工作交接的共享工作区', idempotency_key: 'qoder-task-tracker-v1' }) });
if (!response.ok) throw new Error(`创建 Scope 失败（HTTP ${response.status}），请检查服务版本和认证。`);
const { scope_id: id } = await response.json();
if (typeof id !== 'string' || !id || /[\r\n]/.test(id)) throw new Error('服务未返回有效 Scope ID。');
let env;
try { env = await readFile('.env', 'utf8'); } catch { env = await readFile('.env.example', 'utf8'); }
env = /^POWERCONTEXT_SCOPE_ID=.*$/m.test(env) ? env.replace(/^POWERCONTEXT_SCOPE_ID=.*$/m, `POWERCONTEXT_SCOPE_ID=${id}`) : `${env}\nPOWERCONTEXT_SCOPE_ID=${id}\n`;
await writeFile('.env', env);
console.log(`已将服务返回的 Scope ID 写入 .env：${id}`);
