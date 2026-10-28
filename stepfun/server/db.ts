import 'dotenv/config';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { randomBytes } from 'node:crypto';
import { SeekdbClient, SeekdbAdminClient, Schema, FulltextIndexConfig, VectorIndexConfig } from 'seekdb';
import { MODEL, spaceId, type Precision } from '../shared/model';
import type { KnowledgeBase, Chunk, SearchHit } from '../shared/contracts';

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

// hybridSearch uses SET @search_parm + GET_SQL + SELECT on one SDK session.
// Serialize complete operations, including reads, so concurrent HTTP requests
// cannot overwrite each other's session variables or race refresh with search.
export class SerialQueue {
  private tail: Promise<unknown> = Promise.resolve();
  run<T>(operation: () => Promise<T>): Promise<T> {
    const result = this.tail.then(operation);
    this.tail = result.catch(() => undefined);
    return result;
  }
}

export class Database {
  readonly queue = new SerialQueue();
  client!: SeekdbClient;
  version = '';
  readonly mode = process.env.SEEKDB_MODE ?? 'server';
  async run<T>(operation: () => Promise<T>, retrySafe = true): Promise<T> {
    return this.queue.run(async () => {
      // The local server may restart or close an idle MySQL session. Reconnect
      // before doing work, and only replay operations that are idempotent.
      try { await this.client.execute('SELECT 1'); }
      catch { await this.close().catch(() => undefined); await this.init(); }
      try { return await operation(); }
      catch (error) {
        if (!retrySafe || this.mode !== 'server' || !/closed state|connection.*(?:closed|lost)|ECONNRESET|PROTOCOL_CONNECTION_LOST/i.test(String(error))) throw error;
        await this.close().catch(() => undefined);
        await this.init();
        return operation();
      }
    });
  }
  async init() {
    const database = process.env.SEEKDB_DATABASE ?? 'stepfun';
    if (!/^[a-zA-Z][a-zA-Z0-9_]{0,40}$/.test(database)) throw new Error('SEEKDB_DATABASE 必须为字母、数字或下划线');
    const dbPath = path.resolve(process.env.SEEKDB_PATH ?? '.data/seekdb');
    await mkdir(path.resolve('.data/bindings'), { recursive: true });
    process.env.SEEKDB_BINDINGS_CACHE_DIR ??= path.resolve('.data/bindings');
    const connection = this.mode === 'embedded' ? { path: dbPath } : {
      host: process.env.SEEKDB_HOST ?? '127.0.0.1', port: Number(process.env.SEEKDB_PORT ?? 2881),
      user: process.env.SEEKDB_USER ?? 'root', password: process.env.SEEKDB_PASSWORD ?? '',
    };
    const admin = new SeekdbAdminClient(connection);
    try {
      const all = await admin.listDatabases();
      if (!all.some(db => db.name === database)) await admin.createDatabase(database);
    } finally { await admin.close(); }
    this.client = new SeekdbClient({ ...connection, database });
    const rows = await this.client.execute('SELECT VERSION() AS version');
    this.version = String(rows?.[0]?.version ?? 'unknown');
    await this.client.execute(`CREATE TABLE IF NOT EXISTS stepfun_registry (
      name VARCHAR(32) PRIMARY KEY, title VARCHAR(240) NOT NULL,
      precision_name VARCHAR(8) NOT NULL, space_id VARCHAR(200) NOT NULL,
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )`);
  }
  private async registry(name: string) {
    const rows = await this.client.execute('SELECT * FROM stepfun_registry WHERE name = ?', [name]);
    if (!rows?.length) throw new ApiError(404, '知识库不存在，请重新选择');
    return rows[0];
  }
  private async collection(name: string, space?: string) {
    const entry = await this.registry(name);
    if (entry.space_id !== spaceId(entry.precision_name)) throw new ApiError(409, '该知识库使用其他模型版本，请使用匹配版本的应用或重建知识库');
    if (space && entry.space_id !== space) throw new ApiError(409, '模型精度与知识库不匹配，请切换到该知识库使用的精度');
    return this.client.getCollection({ name, embeddingFunction: null });
  }
  async list(): Promise<KnowledgeBase[]> {
    const entries = await this.client.execute('SELECT * FROM stepfun_registry ORDER BY created_at, name') ?? [];
    const result: KnowledgeBase[] = [];
    for (const entry of entries) {
      const collection = await this.collection(entry.name);
      result.push({ name: entry.name, title: entry.title, precision: entry.precision_name, space: entry.space_id, count: await collection.count() });
    }
    return result;
  }
  async create(title: string, precision: Precision): Promise<KnowledgeBase> {
    const name = `kb_${randomBytes(12).toString('hex')}`;
    await this.client.createCollection({
      name,
      schema: new Schema({
        fulltextIndex: new FulltextIndexConfig('ngram', { ngram_token_size: 2 }),
        vectorIndex: new VectorIndexConfig({ hnsw: { dimension: MODEL.dimension, distance: 'cosine' }, embeddingFunction: null }),
      }),
      embeddingFunction: null,
    });
    try {
      await this.client.execute('INSERT INTO stepfun_registry (name, title, precision_name, space_id) VALUES (?, ?, ?, ?)', [name, title, precision, spaceId(precision)]);
    } catch (error) {
      await this.client.deleteCollection(name);
      throw error;
    }
    return { name, title, precision, space: spaceId(precision), count: 0 };
  }
  async write(name: string, space: string, chunks: Chunk[]) {
    const collection = await this.collection(name, space);
    const start = performance.now();
    await collection.upsert({ ids: chunks.map(c => c.id), documents: chunks.map(c => c.document), embeddings: chunks.map(c => c.embedding), metadatas: chunks.map(c => c.metadata) });
    return { written: chunks.length, writeMs: performance.now() - start, needsRefresh: true };
  }
  async refresh(name: string) {
    const collection = await this.collection(name);
    const start = performance.now();
    // Explicit call: unlike SDK refresh_index(), never silently skip older versions.
    await this.client.execute('CALL dbms_index_manager.refresh()');
    const count = await collection.count();
    return { count, refreshMs: performance.now() - start, searchable: true };
  }
  async search(name: string, request: { space: string; query: string; embedding: number[]; mode: 'hybrid' | 'vector'; k: number }) {
    const collection = await this.collection(name, request.space);
    const start = performance.now();
    const result = request.mode === 'hybrid' ? await collection.hybridSearch({
      query: { whereDocument: { $contains: request.query }, nResults: Math.max(20, request.k) },
      knn: { queryEmbeddings: [request.embedding], nResults: Math.max(20, request.k) },
      rank: { rrf: { rankConstant: 60, rankWindowSize: 50 } },
      nResults: request.k, include: ['documents', 'metadatas'],
    }) : await collection.query({ queryEmbeddings: [request.embedding], nResults: request.k, include: ['documents', 'metadatas'] });
    // Preserve the database's order. This is formatting, not client-side fusion.
    const hits: SearchHit[] = (result.ids[0] ?? []).map((id, i) => ({ id, document: result.documents?.[0]?.[i] ?? '', metadata: result.metadatas?.[0]?.[i] ?? {}, score: result.distances?.[0]?.[i] ?? 0 }));
    return { hits, searchMs: performance.now() - start, mode: request.mode, engine: request.mode === 'hybrid' ? 'seekdb DBMS_HYBRID_SEARCH / RRF' : 'seekdb cosine / HNSW' };
  }
  async close() { if (this.client) await this.client.close(); }
}
