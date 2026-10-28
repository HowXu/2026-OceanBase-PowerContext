// Real seekdb integration checks. Reuses vectors already produced by the
// browser; this script contains no model and is not an application search path.
import assert from 'node:assert/strict';
import { writeFile, mkdir } from 'node:fs/promises';
import { Database } from '../server/db';
import type { Chunk } from '../shared/contracts';

const db = new Database();
let scratchName: string | undefined;
const results: string[] = [];
try {
  await db.init();
  const all = await db.run(() => db.list());
  const source = all.find(kb => kb.title === '我的资料' && kb.count >= 20);
  assert.ok(source, '先在浏览器中向“我的资料”导入示例语料');
  const sourceCollection = await db.client.getCollection({ name: source.name, embeddingFunction: null });
  const data = await sourceCollection.get({ where: { sourceId: 'db-hybrid' }, include: ['documents', 'metadatas', 'embeddings'], limit: 1 });
  assert.ok(data.embeddings?.[0] && data.documents?.[0] && data.metadatas?.[0]);
  const chunk: Chunk = { id: data.ids[0], document: data.documents[0], metadata: data.metadatas[0] as Chunk['metadata'], embedding: data.embeddings[0] };
  const scratch = await db.run(() => db.create('验收临时集合', source.precision), false);
  scratchName = scratch.name;
  assert.equal(scratch.count, 0);
  await db.run(() => db.write(scratch.name, scratch.space, [chunk]));
  await db.run(() => db.write(scratch.name, scratch.space, [chunk]));
  const refreshed = await db.run(() => db.refresh(scratch.name));
  assert.equal(refreshed.count, 1);
  results.push('重复 upsert 幂等：两次写入同一片段后 count=1，显式刷新成功');
  const found = await db.run(() => db.search(scratch.name, { space: scratch.space, query: '混合检索', embedding: chunk.embedding, mode: 'hybrid', k: 5 }));
  assert.equal(found.hits.length, 1);
  assert.equal(found.hits[0].metadata.sourceId, 'db-hybrid');
  assert.equal(found.engine, 'seekdb DBMS_HYBRID_SEARCH / RRF');
  results.push('真实数据库内混合检索返回新写入片段');
  assert.equal(await sourceCollection.count(), source.count);
  results.push('独立集合写入不影响原知识库计数');
  await assert.rejects(db.run(() => db.search(scratch.name, { space: 'wrong-space', query: '混合检索', embedding: chunk.embedding, mode: 'hybrid', k: 5 })), /不匹配/);
  results.push('模型空间不匹配被拒绝');
  await db.client.close();
  const afterClose = await db.run(() => db.search(scratch.name, { space: scratch.space, query: '混合检索', embedding: chunk.embedding, mode: 'hybrid', k: 5 }));
  assert.equal(afterClose.hits[0].id, chunk.id);
  results.push('关闭连接后自动重连，数据库内容与检索结果保持一致');
  await mkdir('docs/evidence', { recursive: true });
  await writeFile('docs/evidence/database-checks.json', JSON.stringify({ at: new Date().toISOString(), mode: db.mode, version: db.version, results }, null, 2));
  console.log(results.join('\n'));
} finally {
  // Delete only the random scratch collection created by this invocation.
  if (scratchName) await db.run(async () => {
    await db.client.deleteCollection(scratchName!);
    await db.client.execute('DELETE FROM stepfun_registry WHERE name = ?', [scratchName]);
  });
  await db.close();
}
