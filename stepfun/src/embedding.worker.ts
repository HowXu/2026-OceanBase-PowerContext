import { env, pipeline, type FeatureExtractionPipeline } from '@huggingface/transformers';
import { MODEL, type Precision, type Backend, type BackendPreference } from '../shared/model';

// Narrow Transformers.js's all-task overload to the one pipeline used here.
const createExtractor = pipeline as unknown as (task: 'feature-extraction', model: string, options: {
  device: Backend; dtype: Precision; local_files_only: boolean; progress_callback?: (progress: unknown) => void;
}) => Promise<FeatureExtractionPipeline>;

env.allowRemoteModels = false;
env.allowLocalModels = true;
env.localModelPath = '/models/';
env.useBrowserCache = true;
env.backends.onnx.wasm!.wasmPaths = '/ort/';
env.backends.onnx.wasm!.numThreads = self.crossOriginIsolated ? Math.min(navigator.hardwareConcurrency || 2, 4) : 1;
// ORT runs inside this dedicated worker; do not create another proxy worker.
env.backends.onnx.wasm!.proxy = false;

let extractor: FeatureExtractionPipeline | null = null;
let backend: Backend = 'wasm';
let precision: Precision = 'q8';
let preference: BackendPreference = 'auto';
let ready: Record<string, unknown> | null = null;

const status = (value: Record<string, unknown>) => self.postMessage({ type: 'status', ...value });
async function dispose() { if (extractor) await extractor.dispose(); extractor = null; ready = null; }
async function load(device: Backend, dtype: Precision) {
  const started = performance.now();
  const instance = await createExtractor('feature-extraction', MODEL.id, {
    device, dtype, local_files_only: true,
    progress_callback: p => status({ phase: 'loading', progress: p }),
  });
  const loadMs = performance.now() - started;
  const warm = performance.now();
  try { await instance(['本地模型预热'], { pooling: 'cls', normalize: true }); }
  catch (error) { await instance.dispose(); throw error; }
  const warmupMs = performance.now() - warm;
  let validationMs = 0;
  let parityCosine: number | null = null;
  if (device === 'webgpu') {
    // Successful execution alone does not guarantee numerical correctness.
    // On some ORT/GPU combinations QInt8 kernels run but produce bad vectors.
    // Compare real browser GPU outputs to an independent FP32 WASM reference.
    const validationStart = performance.now();
    const probes = ['向量和全文索引共同完成数据库混合检索。', MODEL.queryPrefix + '浏览器没有显卡还能计算向量吗？'];
    let reference: FeatureExtractionPipeline | undefined;
    try {
      reference = await createExtractor('feature-extraction', MODEL.id, { device: 'wasm', dtype: 'fp32', local_files_only: true });
      const cpu = (await reference(probes, { pooling: 'cls', normalize: true })).tolist() as number[][];
      const gpu = (await instance(probes, { pooling: 'cls', normalize: true })).tolist() as number[][];
      parityCosine = Math.min(...cpu.map((v, i) => v.reduce((sum, value, j) => sum + value * gpu[i][j], 0) / (Math.hypot(...v) * Math.hypot(...gpu[i]))));
      if (!Number.isFinite(parityCosine) || parityCosine < 0.98) throw new Error(`WebGPU 输出一致性检查失败：与 FP32/WASM 的最小余弦相似度 ${parityCosine.toFixed(5)} < 0.98。请使用 WASM 或新建 FP16 知识库`);
    } catch (error) { await instance.dispose(); throw error; }
    finally { if (reference) await reference.dispose(); }
    validationMs = performance.now() - validationStart;
  }
  return { instance, loadMs, warmupMs, validationMs, parityCosine };
}
async function initialize(options: { precision: Precision; preference: BackendPreference }) {
  if (ready && precision === options.precision && preference === options.preference) return { ...ready, reused: true };
  const initStarted = performance.now();
  await dispose();
  precision = options.precision;
  preference = options.preference;
  let fallback = '';
  let adapterAvailable = false;
  if (preference !== 'wasm') {
    try {
      const gpu = (navigator as unknown as { gpu?: { requestAdapter(): Promise<unknown> } }).gpu;
      adapterAvailable = !!(gpu && await gpu.requestAdapter());
    } catch (error) { fallback = `WebGPU 适配器不可用：${String(error)}`; }
  }
  if (preference === 'webgpu' && !adapterAvailable) throw new Error('当前浏览器没有可用的 WebGPU 适配器');
  let loaded;
  if (preference !== 'wasm' && adapterAvailable) {
    try { loaded = await load('webgpu', precision); backend = 'webgpu'; }
    catch (error) {
      if (preference === 'webgpu') throw error;
      fallback = `WebGPU 模型加载或预热失败，已使用 WASM：${String(error).slice(0, 180)}`;
    }
  }
  if (!loaded) {
    if (!adapterAvailable && preference === 'auto' && !fallback) fallback = '未检测到可用的 WebGPU，已自动使用 WASM';
    loaded = await load('wasm', precision);
    backend = 'wasm';
  }
  extractor = loaded.instance;
  ready = { backend, precision, preference, fallback, adapterAvailable, loadMs: loaded.loadMs, warmupMs: loaded.warmupMs, validationMs: loaded.validationMs, parityCosine: loaded.parityCosine, initMs: performance.now() - initStarted, threads: backend === 'wasm' ? env.backends.onnx.wasm!.numThreads : null, reused: false };
  status({ phase: 'ready', ...ready });
  return ready;
}
async function embed(texts: string[], query = false, batchSize = 8) {
  if (!extractor) throw new Error('请先加载本地模型');
  const start = performance.now();
  const embeddings: number[][] = [];
  for (let i = 0; i < texts.length; i += batchSize) {
    const batch = texts.slice(i, i + batchSize).map(text => query ? MODEL.queryPrefix + text : text);
    let output;
    try { output = await extractor(batch, { pooling: 'cls', normalize: true }); }
    catch (error) {
      // A device can fail after a successful warmup (e.g. GPU device loss).
      if (backend !== 'webgpu' || preference !== 'auto') throw error;
      await dispose();
      const loaded = await load('wasm', precision);
      extractor = loaded.instance; backend = 'wasm';
      ready = { backend, precision, preference, fallback: `GPU 推理失败，已切换 WASM：${String(error).slice(0, 140)}`, loadMs: loaded.loadMs, warmupMs: loaded.warmupMs };
      status({ phase: 'ready', ...ready });
      output = await extractor(batch, { pooling: 'cls', normalize: true });
    }
    const vectors = output.tolist() as number[][];
    if (vectors.some(v => v.length !== MODEL.dimension || v.some(n => !Number.isFinite(n)))) throw new Error('模型输出维度或数值无效');
    embeddings.push(...vectors);
    status({ phase: 'embedding', done: embeddings.length, total: texts.length });
  }
  return { embeddings, embedMs: performance.now() - start, backend, precision };
}
let queue = Promise.resolve();
self.onmessage = event => {
  const { id, type, options, texts, query, batchSize } = event.data;
  queue = queue.then(async () => {
    try {
      const result = type === 'init' ? await initialize(options) : type === 'dispose' ? await dispose() : await embed(texts, query, batchSize);
      self.postMessage({ id, result });
    } catch (error) { self.postMessage({ id, error: error instanceof Error ? error.message : String(error) }); }
  });
};
