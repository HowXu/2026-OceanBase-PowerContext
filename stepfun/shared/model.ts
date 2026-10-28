// Same immutable revision for the downloader, browser worker, API and reports.
export const MODEL = {
  id: 'Xenova/bge-small-zh-v1.5',
  revision: '75c43b069aac4d136ba6bc1122f995fedcfd2781',
  dimension: 512,
  pooling: 'cls',
  normalize: true,
  queryPrefix: '为这个句子生成表示以用于检索相关文章：',
} as const;
export type Precision = 'fp32' | 'q8' | 'fp16';
export type Backend = 'webgpu' | 'wasm';
export type BackendPreference = 'auto' | Backend;
export function spaceId(precision: Precision): string {
  return `${MODEL.id}@${MODEL.revision}:cls:l2:${precision}:chunk350v1`;
}
