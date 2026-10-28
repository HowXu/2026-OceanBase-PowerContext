import test from 'node:test';
import assert from 'node:assert/strict';
import { splitText, prepareDocuments } from '../src/chunk';
import { recallAtK, ndcgAtK, median } from '../src/metrics';
import { searchRequest, writeRequest, collectionName } from '../shared/contracts';
import { SerialQueue } from '../server/db';

test('分块保留全文、重叠且不切断 Unicode 字符', () => {
  const text = '甲😀乙。'.repeat(100);
  const chunks = splitText(text, 35, 5);
  assert.ok(chunks.length > 1);
  assert.ok(chunks.every(chunk => Array.from(chunk).length <= 35));
  assert.ok(chunks.every(chunk => !/[\uD800-\uDBFF]$/.test(chunk) && !/^[\uDC00-\uDFFF]/.test(chunk)));
  let recovered = chunks[0];
  for (const chunk of chunks.slice(1)) recovered += Array.from(chunk).slice(5).join('');
  assert.equal(recovered, text);
});
test('空文本与非法分块参数', () => {
  assert.deepEqual(splitText(' \n\r\n '), []);
  assert.throws(() => splitText('abc', 5, 5));
  assert.throws(() => splitText('abc', 0, 0));
});
test('重复导入 ID 稳定，来源和内容变化保留独立记录', async () => {
  const a = [{ id: 'doc1', title: '资料', text: '原始文档' }];
  const [first] = await prepareDocuments(a);
  const [again] = await prepareDocuments(a);
  const [changed] = await prepareDocuments([{ ...a[0], text: '新文档' }]);
  const [otherSource] = await prepareDocuments([{ ...a[0], id: 'doc2' }]);
  assert.equal(first.id, again.id);
  assert.notEqual(first.id, changed.id);
  assert.notEqual(first.id, otherSource.id);
  assert.equal(first.metadata.sourceId, 'doc1');
});
test('质量指标不因重复召回而增加，顺序影响 nDCG', () => {
  assert.equal(recallAtK(['a', 'a', 'b'], ['a', 'b'], 2), 0.5);
  assert.equal(ndcgAtK(['a'], ['a'], 5), 1);
  assert.ok(ndcgAtK(['x', 'a'], ['a'], 5) < 1);
  assert.equal(ndcgAtK(['x'], [], 5), 0);
  assert.equal(median([9, 1, 5, 3]), 4);
});
test('请求拒绝缺失、错误维数、非有限值和未归一化向量', () => {
  const valid = { space: 'space', query: '问题', embedding: [1, ...Array(511).fill(0)] };
  assert.equal(searchRequest.parse(valid).mode, 'hybrid');
  assert.equal(searchRequest.safeParse({ ...valid, embedding: [1, 2] }).success, false);
  assert.equal(searchRequest.safeParse({ ...valid, embedding: [Infinity, ...Array(511).fill(0)] }).success, false);
  assert.equal(searchRequest.safeParse({ ...valid, embedding: Array(512).fill(1) }).success, false);
  assert.equal(searchRequest.safeParse({ ...valid, k: 999 }).success, false);
  assert.equal(writeRequest.safeParse({ space: 'space', chunks: [] }).success, false);
  assert.equal(collectionName.safeParse("kb_a'; DROP TABLE x;--").success, false);
});
test('并发混合检索不会交错同一数据库会话，错误不会阻塞队列', async () => {
  const queue = new SerialQueue();
  const calls: string[] = [];
  const first = queue.run(async () => { calls.push('set-A'); await Promise.resolve(); calls.push('get-A'); throw new Error('failed'); });
  const second = queue.run(async () => { calls.push('set-B'); await Promise.resolve(); calls.push('get-B'); return 7; });
  await assert.rejects(first);
  assert.equal(await second, 7);
  assert.deepEqual(calls, ['set-A', 'get-A', 'set-B', 'get-B']);
});
