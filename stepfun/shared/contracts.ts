import { z } from 'zod';
import { MODEL } from './model';

export const collectionName = z.string().regex(/^kb_[a-f0-9]{24}$/, '集合 ID 无效');
export const vector = z.array(z.number().finite()).length(MODEL.dimension).refine(
  v => { const norm = Math.hypot(...v); return norm > 0.98 && norm < 1.02; },
  '需要浏览器生成的 L2 归一化向量',
);
export const createCollection = z.object({
  title: z.string().trim().min(1).max(60),
  precision: z.enum(['fp32', 'q8', 'fp16']),
});
export const chunkSchema = z.object({
  id: z.string().regex(/^[a-f0-9]{64}_[0-9]+$/),
  document: z.string().min(1).max(2000),
  embedding: vector,
  metadata: z.object({
    source: z.string().min(1).max(255),
    sourceId: z.string().max(255),
    chunk: z.number().int().min(0),
    total: z.number().int().min(1),
  }),
});
export const writeRequest = z.object({
  space: z.string().max(200),
  chunks: z.array(chunkSchema).min(1).max(64),
});
export const searchRequest = z.object({
  space: z.string().max(200),
  query: z.string().trim().min(1).max(1000),
  embedding: vector,
  mode: z.enum(['hybrid', 'vector']).default('hybrid'),
  k: z.number().int().min(1).max(20).default(5),
});
export type Chunk = z.infer<typeof chunkSchema>;
export interface KnowledgeBase { name: string; title: string; precision: 'fp32' | 'q8' | 'fp16'; space: string; count: number }
export interface SearchHit { id: string; document: string; metadata: Record<string, unknown>; score: number }
