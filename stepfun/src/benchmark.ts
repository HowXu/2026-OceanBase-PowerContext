import dataset from '../data/evaluation.json';
import { MODEL, type Precision, type BackendPreference } from '../shared/model';
import type { KnowledgeBase, SearchHit } from '../shared/contracts';
import { api } from './api';
import { prepareDocuments } from './chunk';
import { LocalEmbedding } from './embedding';
import { recallAtK, ndcgAtK, median } from './metrics';

interface Configuration { precision: Precision; backend: BackendPreference; batch: number; refresh: 'end' | 'batch' }
const configurations: Configuration[] = [
  { precision: 'fp32', backend: 'wasm', batch: 1, refresh: 'end' },
  { precision: 'q8', backend: 'wasm', batch: 1, refresh: 'end' },
  { precision: 'q8', backend: 'wasm', batch: 8, refresh: 'end' },
  { precision: 'q8', backend: 'wasm', batch: 8, refresh: 'batch' },
  { precision: 'fp32', backend: 'webgpu', batch: 8, refresh: 'end' },
  { precision: 'q8', backend: 'webgpu', batch: 8, refresh: 'end' },
  { precision: 'fp16', backend: 'webgpu', batch: 8, refresh: 'end' },
];
export async function runBenchmark(embedding: LocalEmbedding, update: (message: string) => void) {
  const manifest = await api<{ files: { file: string; bytes: number; sha256: string }[] }>('/api/model-manifest');
  const health = await api<Record<string, unknown>>('/api/health');
  const report = {
    schemaVersion: 1, startedAt: new Date().toISOString(), model: MODEL, dataset,
    environment: { userAgent: navigator.userAgent, hardwareConcurrency: navigator.hardwareConcurrency, deviceMemory: (navigator as unknown as { deviceMemory?: number }).deviceMemory, crossOriginIsolated, database: health, note: '首次网络下载由 models:download 单独计时；此处模型本地加载可能命中浏览器缓存。质量以 sourceId 去重后计算。' },
    manifest, configurations: [] as Record<string, unknown>[],
  };
  const chunks = await prepareDocuments(dataset.documents);
  for (const [index, config] of configurations.entries()) {
    update(`配置 ${index + 1}/${configurations.length}：${config.precision} / ${config.backend} / batch ${config.batch}`);
    await embedding.dispose();
    let model;
    try { model = await embedding.init(config.precision, config.backend); }
    catch (error) {
      report.configurations.push({ config, skipped: true, reason: String(error) });
      continue;
    }
    const file = config.precision === 'q8' ? 'onnx/model_quantized.onnx' : config.precision === 'fp16' ? 'onnx/model_fp16.onnx' : 'onnx/model.onnx';
    const modelBytes = manifest.files.find(item => item.file === file)!.bytes;
    const baselineBytes = manifest.files.find(item => item.file === 'onnx/model.onnx')!.bytes;
    const kb = await api<KnowledgeBase>('/api/collections', { title: `评测 ${config.precision} ${config.backend} b${config.batch} ${config.refresh} ${Date.now()}`, precision: config.precision });
    const trials: Record<string, unknown>[] = [];
    for (let repeat = 0; repeat < 3; repeat++) {
      let embeddingMs = 0, writeMs = 0, refreshMs = 0;
      const importStart = performance.now();
      for (let offset = 0; offset < chunks.length; offset += config.batch) {
        const batch = chunks.slice(offset, offset + config.batch);
        const encoded = await embedding.embed(batch.map(c => c.document), false, config.batch);
        embeddingMs += encoded.embedMs;
        const written = await api<{ writeMs: number }>(`/api/collections/${kb.name}/chunks`, { space: kb.space, chunks: batch.map((chunk, i) => ({ ...chunk, embedding: encoded.embeddings[i] })) });
        writeMs += written.writeMs;
        if (config.refresh === 'batch') refreshMs += (await api<{ refreshMs: number }>(`/api/collections/${kb.name}/refresh`, {})).refreshMs;
      }
      if (config.refresh === 'end') refreshMs += (await api<{ refreshMs: number }>(`/api/collections/${kb.name}/refresh`, {})).refreshMs;
      const importMs = performance.now() - importStart;
      const queries = [];
      for (const query of dataset.queries) {
        // Measure vector and hybrid separately; never fuse their result sets.
        for (const mode of ['vector', 'hybrid'] as const) {
          const start = performance.now();
          const encoded = await embedding.embed([query.text], true, 1);
          const result = await api<{ hits: SearchHit[]; searchMs: number }>(`/api/collections/${kb.name}/search`, { space: kb.space, query: query.text, embedding: encoded.embeddings[0], k: 5, mode });
          const ids = [...new Set(result.hits.map(h => String(h.metadata.sourceId)))];
          queries.push({ text: query.text, mode, ids, relevant: query.relevant, recallAt5: recallAtK(ids, query.relevant, 5), ndcgAt5: ndcgAtK(ids, query.relevant, 5), embeddingMs: encoded.embedMs, searchMs: result.searchMs, endToEndMs: performance.now() - start });
        }
      }
      trials.push({ repeat, embeddingMs, writeMs, refreshMs, importMs, chunksPerSecond: chunks.length * 1000 / embeddingMs, queries });
      update(`${config.precision} / ${config.backend}：第 ${repeat + 1}/3 轮完成`);
    }
    const queryRows = trials.flatMap(t => t.queries as { mode: string; recallAt5: number; ndcgAt5: number; searchMs: number; endToEndMs: number }[]);
    const mean = (values: number[]) => values.reduce((a, b) => a + b, 0) / values.length;
    const summary = Object.fromEntries(['hybrid', 'vector'].map(mode => {
      const rows = queryRows.filter(row => row.mode === mode);
      return [mode, { recallAt5: mean(rows.map(r => r.recallAt5)), ndcgAt5: mean(rows.map(r => r.ndcgAt5)), searchMedianMs: median(rows.map(r => r.searchMs)), endToEndMedianMs: median(rows.map(r => r.endToEndMs)) }];
    }));
    report.configurations.push({ config, model, modelBytes, compressionRatio: baselineBytes / modelBytes, collection: kb.name, trials, summary, medianChunksPerSecond: median(trials.map(t => t.chunksPerSecond as number)) });
  }
  return { ...report, completedAt: new Date().toISOString() };
}
export function downloadJson(value: unknown, filename: string) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' }));
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
