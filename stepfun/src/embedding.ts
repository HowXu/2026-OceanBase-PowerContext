import type { Precision, Backend, BackendPreference } from '../shared/model';
export interface ModelReady { backend: Backend; precision: Precision; preference: BackendPreference; fallback: string; loadMs: number; warmupMs: number; validationMs: number; parityCosine: number | null; initMs: number; threads: number; reused: boolean }
export interface EmbeddingResult { embeddings: number[][]; embedMs: number; backend: Backend; precision: Precision }
export class LocalEmbedding {
  private worker = new Worker(new URL('./embedding.worker.ts', import.meta.url), { type: 'module' });
  private serial = 0;
  private pending = new Map<number, { resolve(value: unknown): void; reject(error: Error): void }>();
  onStatus: (status: Record<string, unknown>) => void = () => {};
  constructor() {
    this.worker.onmessage = event => {
      const { id, result, error, type } = event.data;
      if (type === 'status') { this.onStatus(event.data); return; }
      const job = this.pending.get(id);
      if (!job) return;
      this.pending.delete(id);
      if (error) job.reject(new Error(error)); else job.resolve(result);
    };
    this.worker.onerror = event => {
      for (const job of this.pending.values()) job.reject(new Error(event.message || '模型工作线程意外退出，请刷新页面'));
      this.pending.clear();
    };
  }
  private request<T>(body: Record<string, unknown>): Promise<T> {
    const id = ++this.serial;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve: value => resolve(value as T), reject });
      this.worker.postMessage({ id, ...body });
    });
  }
  init(precision: Precision, preference: BackendPreference = 'auto') { return this.request<ModelReady>({ type: 'init', options: { precision, preference } }); }
  embed(texts: string[], query = false, batchSize = 8) { return this.request<EmbeddingResult>({ type: 'embed', texts, query, batchSize }); }
  dispose() { return this.request<void>({ type: 'dispose' }); }
}
