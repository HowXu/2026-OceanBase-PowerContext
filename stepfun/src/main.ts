import './style.css';
import dataset from '../data/evaluation.json';
import { MODEL, type Precision, type BackendPreference } from '../shared/model';
import type { KnowledgeBase, SearchHit } from '../shared/contracts';
import { LocalEmbedding, type ModelReady } from './embedding';
import { api } from './api';
import { prepareDocuments, type TextDocument } from './chunk';
import { runBenchmark, downloadJson } from './benchmark';

const $ = <T extends HTMLElement = HTMLElement>(selector: string) => document.querySelector<T>(selector)!;
const escape = (value: unknown) => String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!));
const ms = (n: number) => n < 1000 ? `${n.toFixed(0)} ms` : `${(n / 1000).toFixed(2)} s`;
const embedding = new LocalEmbedding();
let collections: KnowledgeBase[] = [];
let selected: KnowledgeBase | undefined;
let ready: ModelReady | undefined;
let busy = false;
let healthReady = false;
let pickedFiles: File[] = [];
let metrics: Record<string, unknown>[] = [];
let benchmarkReport: Awaited<ReturnType<typeof runBenchmark>> | undefined;

$('#app').innerHTML = `
  <aside class="sidebar">
    <a class="brand" href="/" aria-label="拾知首页"><span class="brand-mark">拾</span><span>拾知<small>把资料留在手边</small></span></a>
    <div class="library-heading"><h2>我的知识库</h2><button id="new-library" class="icon-button" title="新建知识库" aria-label="新建知识库">＋</button></div>
    <div id="libraries" class="libraries"><p class="muted">正在连接…</p></div>
    <div class="sidebar-bottom"><span class="local-dot"></span> 在你的设备上推理<p>向量与原文存储于本机 seekdb</p><span id="database-status" class="connection">连接数据库中</span></div>
  </aside>
  <main>
    <header class="topbar"><span class="breadcrumb">个人知识空间 <span>/</span> <b id="current-title">选择知识库</b></span><div class="backend"><span class="status-dot" id="model-dot"></span><span id="backend-label">模型尚未加载</span></div></header>
    <div class="workspace">
      <section class="intro"><div><span class="subtle-label">本地知识，随时可寻</span><h1>让资料，成为线索。</h1><p>导入文档，用自己的话提问。计算就在浏览器里发生。</p></div><div class="book-art" aria-hidden="true"><i></i><i></i><i></i><span>知</span></div></section>
      <nav class="tabs" aria-label="工作区"><button class="tab active" data-tab="search" aria-selected="true">检索资料</button><button class="tab" data-tab="evaluation" aria-selected="false">性能与质量</button><span id="document-count">0 个片段</span></nav>
      <div id="notice" class="notice" role="status" aria-live="polite" hidden></div>
      <div id="search-panel" class="panel-grid">
        <section class="search-area">
          <form id="search-form"><label class="sr-only" for="query">输入查询</label><div class="search-box"><span aria-hidden="true">⌕</span><input id="query" maxlength="1000" placeholder="输入问题、关键词，或一段模糊的记忆…" autocomplete="off"><button class="primary" id="search-button" type="submit">检索</button></div><div class="search-options"><label>检索方式 <select id="search-mode"><option value="hybrid">混合检索</option><option value="vector">语义检索</option></select></label><label>结果数量 <select id="top-k"><option>5</option><option>10</option><option>20</option></select></label></div></form>
          <div class="result-heading"><h2 id="result-title">从你的资料开始</h2><span id="search-timing"></span></div>
          <div id="results"><div class="empty-state"><div class="empty-icon" aria-hidden="true">⌘</div><h3>给问题一个有据可循的答案</h3><p>在右侧导入文本，或载入示例资料。<br>检索结果会保留来源和对应原文。</p><button id="demo-import" class="secondary">导入 20 篇示例资料</button><div class="example-queries"><span>试着问问</span><button data-query="浏览器没有可用 GPU 还能计算向量吗？">没有 GPU 也能运行吗？</button><button data-query="关键词和语义结果怎样一起排序？">怎样结合关键词与语义？</button></div></div></div>
        </section>
        <aside class="import-area"><div class="section-heading"><h2>添加资料</h2><span>文本 / Markdown</span></div><label class="dropzone" id="dropzone" for="files"><span class="upload-icon" aria-hidden="true">↥</span><strong>选择文件，或拖放到这里</strong><span>支持 .txt、.md · 单个不超过 2 MB</span><input id="files" type="file" accept=".txt,.md,text/plain,text/markdown" multiple></label><div id="file-list" class="file-list"></div><div class="or-divider">也可以直接粘贴</div><label class="field-label" for="document-title">资料名称</label><input id="document-title" class="text-input" maxlength="200" placeholder="例如：项目笔记"><label class="sr-only" for="document-text">资料正文</label><textarea id="document-text" placeholder="粘贴需要保存的内容…" rows="6"></textarea><button id="import-button" class="primary full">向量化并导入</button><div id="import-progress" hidden><progress max="100" value="0"></progress><span id="progress-label"></span></div><button id="refresh-button" class="text-button">刷新当前知识库索引</button><p class="privacy-note"><span aria-hidden="true">◈</span> 原文、向量写入本机 seekdb。<br>文档与查询的向量均在浏览器计算。</p></aside>
      </div>
      <section id="evaluation-panel" hidden>
        <div class="evaluation-heading"><div><h2>让优化有据可查</h2><p>固定 20 篇资料、10 个查询，重复 3 轮。比较精度、批大小、后端及刷新策略。</p></div><button id="benchmark-button" class="primary">运行完整评测</button></div>
        <p class="muted">评测会创建独立知识库。首次需加载 FP32 / INT8 / FP16 模型，可能需要几分钟；不可用的 WebGPU 配置会记录原因。</p>
        <div class="table-wrap"><table><thead><tr><th>精度 / 后端</th><th>批大小</th><th>模型体积</th><th>吞吐 / 秒</th><th>检索中位数</th><th>查询中位数</th><th>Recall@5</th><th>nDCG@5</th></tr></thead><tbody id="benchmark-results"><tr><td colspan="8" class="table-empty">运行后展示实测数据，原始记录可导出。</td></tr></tbody></table></div>
        <div class="evaluation-actions"><button id="export-report" class="secondary" disabled>导出评测 JSON</button><button id="export-metrics" class="secondary">导出本次操作记录</button><button id="export-dataset" class="text-button">下载测试数据</button></div><p id="benchmark-status" role="status"></p>
        <h3>本次操作耗时</h3><div id="operation-metrics" class="operation-metrics">完成导入或查询后显示各阶段耗时。</div>
      </section>
      <details class="model-settings"><summary>本地推理设置 <span>BGE small zh v1.5 / 512 维</span></summary><div class="settings-row"><label>模型精度 <select id="precision"><option value="fp16">FP16（WebGPU 推荐）</option><option value="q8">INT8（WASM 推荐）</option><option value="fp32">FP32 基线</option></select></label><label>计算后端 <select id="backend"><option value="auto">自动：优先 WebGPU</option><option value="wasm">WASM（验证降级路径）</option><option value="webgpu">仅 WebGPU（评测）</option></select></label><label>批大小 <select id="batch-size"><option>8</option><option>1</option><option>4</option><option>16</option></select></label><button id="load-model" class="secondary">加载模型</button></div><p id="model-detail" class="muted">模型只从本机静态文件加载。知识库创建后固定模型精度；切换精度请新建知识库。</p></details>
    </div>
    <footer><span>拾知 / seekdb × 端侧 AI</span><span>资料有出处，推理在本地。</span></footer>
  </main>
  <dialog id="create-dialog"><form id="create-form"><h2>新建知识库</h2><label for="library-name">知识库名称</label><input id="library-name" class="text-input" maxlength="60" required placeholder="例如：研发资料"><label for="library-precision">模型精度</label><select id="library-precision"><option value="fp16">FP16（适合 WebGPU）</option><option value="q8">INT8（体积更小）</option><option value="fp32">FP32（评测基线）</option></select><p class="muted">不同精度保存在独立集合中，避免向量空间混用。</p><div class="dialog-actions"><button id="cancel-create" type="button" class="secondary">取消</button><button type="submit" class="primary">创建</button></div></form></dialog>
`;

function notice(message: string, isError = false) {
  $('#notice').hidden = false; $('#notice').textContent = message;
  $('#notice').classList.toggle('error', isError);
}
function lock(value: boolean) {
  busy = value;
  document.querySelectorAll<HTMLButtonElement | HTMLSelectElement | HTMLInputElement | HTMLTextAreaElement>('button,select,input,textarea').forEach(el => { el.disabled = value; });
  if (!value) $('#export-report').toggleAttribute('disabled', !benchmarkReport);
}
async function task(operation: () => Promise<void>) {
  if (busy) return;
  lock(true);
  try { await operation(); } catch (error) { notice(error instanceof Error ? error.message : String(error), true); }
  finally { lock(false); }
}
function renderLibraries() {
  const render = (kb: KnowledgeBase) => `<button class="library ${selected?.name === kb.name ? 'selected' : ''}" data-library="${kb.name}"><span class="library-icon" aria-hidden="true">▤</span><span>${escape(kb.title)}<small>${kb.precision.toUpperCase()} / ${kb.count} 个片段</small></span></button>`;
  const regular = collections.filter(kb => !kb.title.startsWith('评测 '));
  const evaluation = collections.filter(kb => kb.title.startsWith('评测 '));
  $('#libraries').innerHTML = collections.length ? regular.map(render).join('') + (evaluation.length ? `<details class="evaluation-libraries" ${selected?.title.startsWith('评测 ') ? 'open' : ''}><summary>评测知识库（${evaluation.length}）</summary>${evaluation.map(render).join('')}</details>` : '') : '<p class="sidebar-empty">创建一个知识库，<br>收好你的第一份资料。</p>';
  document.querySelectorAll<HTMLButtonElement>('[data-library]').forEach(el => { el.disabled = busy; el.onclick = () => { if (!busy) selectLibrary(el.dataset.library!); }; });
  $('#current-title').textContent = selected?.title ?? '选择知识库';
  $('#document-count').textContent = `${selected?.count ?? 0} 个片段`;
}
function selectLibrary(name: string) {
  selected = collections.find(kb => kb.name === name);
  $('#import-progress').hidden = true;
  $('#notice').hidden = true;
  if (selected) {
    localStorage.setItem('stepfun-selected', selected.name);
    $<HTMLSelectElement>('#precision').value = selected.precision;
    if (ready?.precision !== selected.precision) {
      $('#backend-label').textContent = `${selected.precision.toUpperCase()} / 模型待加载`;
      $('#model-dot').classList.remove('ready');
    }
    $('#results').innerHTML = `<div class="empty-state compact"><div class="empty-icon">⌕</div><h3>${escape(selected.title)}</h3><p>${selected.count ? '输入问题，检索这份知识库。' : '导入文本或示例资料后开始检索。'}</p><button id="empty-demo" class="secondary">导入 20 篇示例资料</button></div>`;
    $('#empty-demo').onclick = () => task(() => importDocuments(dataset.documents));
  }
  $('#result-title').textContent = '当前知识库'; $('#search-timing').textContent = '';
  renderLibraries();
}
async function reloadLibraries() {
  const name = selected?.name ?? localStorage.getItem('stepfun-selected');
  collections = await api<KnowledgeBase[]>('/api/collections');
  selected = collections.find(kb => kb.name === name) ?? collections[0];
  renderLibraries();
}
async function requireLibrary() {
  if (!healthReady) throw new Error('seekdb 尚未就绪，请查看左下角数据库连接状态');
  if (!selected) {
    selected = await api<KnowledgeBase>('/api/collections', { title: '我的资料', precision: $<HTMLSelectElement>('#precision').value });
    collections.push(selected); localStorage.setItem('stepfun-selected', selected.name); renderLibraries();
  }
  return selected;
}
async function ensureModel(precision?: Precision) {
  const dtype = precision ?? $<HTMLSelectElement>('#precision').value as Precision;
  ready = await embedding.init(dtype, $<HTMLSelectElement>('#backend').value as BackendPreference);
  $('#backend-label').textContent = `${ready.backend.toUpperCase()} / ${dtype.toUpperCase()}`;
  $('#model-dot').classList.add('ready');
  $('#model-detail').textContent = `${MODEL.id} @ ${MODEL.revision.slice(0, 8)}；加载 ${ms(ready.loadMs)}，预热 ${ms(ready.warmupMs)}。${ready.fallback || '当前后端已通过真实推理预热。'}`;
  return ready;
}
embedding.onStatus = status => {
  if (status.phase === 'ready') {
    $('#backend-label').textContent = `${String(status.backend).toUpperCase()} / ${String(status.precision).toUpperCase()}`;
    $('#model-dot').classList.add('ready');
    if (status.fallback) $('#model-detail').textContent = String(status.fallback);
  }
  if (status.phase === 'loading') {
    $('#backend-label').textContent = '正在加载本地模型…';
    const p = status.progress as { file?: string; progress?: number };
    if (p.file) $('#model-detail').textContent = `${p.file} ${p.progress ? `${p.progress.toFixed(0)}%` : ''}`;
  }
};

function record(value: Record<string, unknown>) {
  metrics.push({ at: new Date().toISOString(), ...value });
  $('#operation-metrics').innerHTML = metrics.slice(-6).reverse().map(item => `<div><b>${item.type === 'import' ? '资料导入' : '检索'}</b>${Object.entries(item).filter(([key, value]) => key.endsWith('Ms') && typeof value === 'number').map(([key, value]) => `<span>${escape(key)} <strong>${ms(value as number)}</strong></span>`).join('')}</div>`).join('');
}
async function importDocuments(documents: TextDocument[]) {
  if (!documents.length || documents.every(doc => !doc.text.trim())) throw new Error('请选择文件或粘贴资料正文');
  const kb = await requireLibrary();
  const started = performance.now();
  notice('正在准备资料并加载本地模型…');
  const model = await ensureModel(kb.precision);
  const chunks = await prepareDocuments(documents);
  if (!chunks.length) throw new Error('资料没有可导入的文本内容');
  $('#import-progress').hidden = false;
  let embedMs = 0, writeMs = 0;
  const batchSize = Number($<HTMLSelectElement>('#batch-size').value);
  for (let offset = 0; offset < chunks.length; offset += 32) {
    const batch = chunks.slice(offset, offset + 32);
    const result = await embedding.embed(batch.map(chunk => chunk.document), false, batchSize);
    embedMs += result.embedMs;
    const written = await api<{ writeMs: number }>(`/api/collections/${kb.name}/chunks`, { space: kb.space, chunks: batch.map((chunk, i) => ({ ...chunk, embedding: result.embeddings[i] })) });
    writeMs += written.writeMs;
    const done = Math.min(offset + 32, chunks.length);
    $<HTMLProgressElement>('#import-progress progress').value = done * 100 / chunks.length;
    $('#progress-label').textContent = `已写入 ${done}/${chunks.length} 个片段，等待统一刷新`;
  }
  $('#progress-label').textContent = '正在刷新 seekdb 索引…';
  const refreshed = await api<{ refreshMs: number; count: number }>(`/api/collections/${kb.name}/refresh`, {});
  await reloadLibraries();
  $('#progress-label').textContent = `${chunks.length} 个片段已导入，索引已刷新`;
  notice(`已导入 ${documents.length} 篇资料、${chunks.length} 个片段。当前知识库共 ${refreshed.count} 个片段，可以开始检索。`);
  record({ type: 'import', collection: kb.name, precision: kb.precision, backend: ready?.backend, batchSize, modelLoadMs: model.reused ? 0 : model.loadMs, warmupMs: model.reused ? 0 : model.warmupMs, embeddingMs: embedMs, writeMs, refreshMs: refreshed.refreshMs, endToEndMs: performance.now() - started, chunks: chunks.length });
}
async function search() {
  const query = $<HTMLInputElement>('#query').value.trim();
  if (!query) throw new Error('请输入要查找的问题或关键词');
  const kb = await requireLibrary();
  const start = performance.now();
  const model = await ensureModel(kb.precision);
  const encoded = await embedding.embed([query], true, 1);
  const result = await api<{ hits: SearchHit[]; searchMs: number; engine: string }>(`/api/collections/${kb.name}/search`, { space: kb.space, query, embedding: encoded.embeddings[0], mode: $<HTMLSelectElement>('#search-mode').value, k: Number($<HTMLSelectElement>('#top-k').value) });
  $('#result-title').textContent = `找到 ${result.hits.length} 条线索`;
  $('#results').innerHTML = result.hits.length ? result.hits.map((hit, i) => `<article class="result"><div class="result-index">${String(i + 1).padStart(2, '0')}</div><div class="result-body"><div class="result-meta">${escape(hit.metadata.source)}<span>片段 ${Number(hit.metadata.chunk) + 1} / ${escape(hit.metadata.total)}</span></div><p>${escape(hit.document)}</p><details><summary>查看完整片段与来源</summary><pre>${escape(hit.document)}</pre><small>文档 ID：${escape(hit.metadata.sourceId)}<br>片段 ID：${escape(hit.id)}<br>数据库返回值：${hit.score.toFixed(6)}（以数据库排序为准）</small></details></div></article>`).join('') : '<div class="empty-state"><h3>还没有找到相关资料</h3><p>确认已导入到当前知识库，或试试更具体的关键词。</p></div>';
  // Include DOM update and the next animation frame in user-visible latency.
  await new Promise<void>(resolve => requestAnimationFrame(() => resolve()));
  const totalMs = performance.now() - start;
  $('#search-timing').textContent = `本地向量 ${ms(encoded.embedMs)} / 检索 ${ms(result.searchMs)} / 总计 ${ms(totalMs)}`;
  notice(`${result.engine}；当前使用 ${encoded.backend.toUpperCase()}。`);
  record({ type: 'search', collection: kb.name, query, backend: encoded.backend, precision: kb.precision, modelLoadMs: model.reused ? 0 : model.loadMs, queryEmbeddingMs: encoded.embedMs, searchMs: result.searchMs, endToEndMs: totalMs });
}

$('#search-form').onsubmit = event => { event.preventDefault(); void task(search); };
$('#load-model').onclick = () => task(async () => { await ensureModel(selected?.precision); notice('模型已加载并完成预热，可以开始导入或检索。'); });
$('#demo-import').onclick = () => task(() => importDocuments(dataset.documents));
document.querySelectorAll<HTMLButtonElement>('[data-query]').forEach(el => { el.onclick = () => { $<HTMLInputElement>('#query').value = el.dataset.query!; }; });
$<HTMLSelectElement>('#precision').onchange = () => {
  if (selected) { $<HTMLSelectElement>('#precision').value = selected.precision; notice('当前知识库的模型精度已固定。如需更换精度，请新建知识库。'); }
};
function showFiles(files: File[]) {
  if (files.length > 100 || files.reduce((n, file) => n + file.size, 0) > 10 * 1024 * 1024) { notice('每次最多 100 个文件、总共 10 MB，请分批导入', true); return; }
  const invalid = files.find(f => !/\.(txt|md)$/i.test(f.name) || f.size > 2 * 1024 * 1024);
  if (invalid) { notice(`${invalid.name}：仅支持不超过 2 MB 的 .txt / .md 文件`, true); return; }
  pickedFiles = files;
  $('#file-list').textContent = files.map(f => f.name).join('、');
}
$<HTMLInputElement>('#files').onchange = event => showFiles(Array.from((event.target as HTMLInputElement).files ?? []));
$('#dropzone').ondragover = event => { event.preventDefault(); $('#dropzone').classList.add('dragover'); };
$('#dropzone').ondragleave = () => $('#dropzone').classList.remove('dragover');
$('#dropzone').ondrop = event => { event.preventDefault(); $('#dropzone').classList.remove('dragover'); if (!busy) showFiles(Array.from(event.dataTransfer?.files ?? [])); };
$('#import-button').onclick = () => task(async () => {
  const decoder = new TextDecoder('utf-8', { fatal: true });
  const documents: TextDocument[] = [];
  for (const file of pickedFiles) {
    try { documents.push({ id: file.name, title: file.name, text: decoder.decode(await file.arrayBuffer()) }); }
    catch { throw new Error(`${file.name} 不是有效 UTF-8 文本，请转换编码后导入`); }
  }
  const text = $<HTMLTextAreaElement>('#document-text').value;
  const title = $<HTMLInputElement>('#document-title').value.trim() || '粘贴的资料';
  if (text.length > 2 * 1024 * 1024) throw new Error('粘贴内容过长，请拆分资料');
  if (text.trim()) documents.push({ id: title, title, text });
  await importDocuments(documents);
});
$('#refresh-button').onclick = () => task(async () => {
  const kb = await requireLibrary();
  const result = await api<{ count: number }>(`/api/collections/${kb.name}/refresh`, {});
  await reloadLibraries(); notice(`索引刷新完成，当前 ${result.count} 个片段可检索。`);
});
$('#new-library').onclick = () => { $<HTMLDialogElement>('#create-dialog').showModal(); $<HTMLInputElement>('#library-name').focus(); };
$('#cancel-create').onclick = () => $<HTMLDialogElement>('#create-dialog').close();
$('#create-form').onsubmit = event => {
  event.preventDefault(); void task(async () => {
    const kb = await api<KnowledgeBase>('/api/collections', { title: $<HTMLInputElement>('#library-name').value, precision: $<HTMLSelectElement>('#library-precision').value });
    collections.push(kb); selectLibrary(kb.name); $<HTMLDialogElement>('#create-dialog').close(); notice(`已创建“${kb.title}”`);
  });
};
document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(button => { button.onclick = () => {
  document.querySelectorAll<HTMLButtonElement>('[data-tab]').forEach(b => { b.classList.toggle('active', b === button); b.setAttribute('aria-selected', String(b === button)); });
  $('#search-panel').hidden = button.dataset.tab !== 'search'; $('#evaluation-panel').hidden = button.dataset.tab !== 'evaluation';
}; });
$('#export-metrics').onclick = () => downloadJson({ model: MODEL, environment: navigator.userAgent, records: metrics }, 'operation-metrics.json');
$('#export-dataset').onclick = () => downloadJson(dataset, 'evaluation-dataset.json');
$('#export-report').onclick = () => downloadJson(benchmarkReport, 'benchmark.json');
$('#benchmark-button').onclick = () => task(async () => {
  notice('正在运行真实浏览器推理与 seekdb 检索评测，请保持页面打开。');
  try {
    benchmarkReport = await runBenchmark(embedding, message => { $('#benchmark-status').textContent = message; });
    $('#benchmark-results').innerHTML = benchmarkReport.configurations.map(row => {
      const c = row.config as { precision: string; backend: string; batch: number; refresh: string };
      if (row.skipped) return `<tr><td>${c.precision} / ${c.backend}</td><td colspan="7">跳过：${escape(row.reason)}</td></tr>`;
      const summary = (row.summary as Record<string, Record<string, number>>).hybrid;
      return `<tr><td>${c.precision.toUpperCase()} / ${c.backend}<small>${c.refresh === 'end' ? '统一刷新' : '逐批刷新'}</small></td><td>${c.batch}</td><td>${((row.modelBytes as number) / 1048576).toFixed(2)} MiB</td><td>${(row.medianChunksPerSecond as number).toFixed(1)}</td><td>${ms(summary.searchMedianMs)}</td><td>${ms(summary.endToEndMedianMs)}</td><td>${(summary.recallAt5 * 100).toFixed(1)}%</td><td>${summary.ndcgAt5.toFixed(3)}</td></tr>`;
    }).join('');
    $('#benchmark-status').textContent = '评测完成。表格为混合检索指标；JSON 包含两种检索模式、三轮原始记录和环境信息。';
    notice('评测完成，可以导出原始记录。');
  } finally {
    await reloadLibraries();
    if (selected) await ensureModel(selected.precision);
  }
});
async function connect() {
  const health = await api<{ ready: boolean; mode: string; error: string }>('/api/health');
  healthReady = health.ready;
  $('#database-status').textContent = health.ready ? `seekdb 已连接 / ${health.mode}` : health.error ? 'seekdb 连接失败' : 'seekdb 正在启动';
  if (health.ready) {
    await reloadLibraries(); if (selected) selectLibrary(selected.name);
  } else {
    if (health.error) notice(`数据库启动失败：${health.error}。查看 README 的本地 server 配置后重新启动。`, true);
    setTimeout(() => { void connect().catch(error => notice(error.message, true)); }, 2500);
  }
}
void connect().catch(error => notice(error.message, true));
