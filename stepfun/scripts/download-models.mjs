import { mkdir, writeFile, stat, rename } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { Readable } from 'node:stream';
import { pipeline } from 'node:stream/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

const model = 'Xenova/bge-small-zh-v1.5';
const revision = '75c43b069aac4d136ba6bc1122f995fedcfd2781';
const root = path.resolve('public/models', model);
const files = ['config.json', 'tokenizer.json', 'tokenizer_config.json', 'special_tokens_map.json', 'vocab.txt', 'onnx/model_quantized.onnx', 'onnx/model_fp16.onnx', 'onnx/model.onnx'];
// Only public immutable model artifacts are downloaded. No inference runs here.
const metadataResponse = await fetch(`https://huggingface.co/api/models/${model}/revision/${revision}?blobs=true`);
if (!metadataResponse.ok) throw new Error(`模型清单请求失败: ${metadataResponse.status}`);
const metadata = await metadataResponse.json();
const started = performance.now();
const manifest = { model, revision, startedAt: new Date().toISOString(), files: [] };
for (const file of files) {
  const info = metadata.siblings.find(item => item.rfilename === file);
  if (!info?.size) throw new Error(`模型清单缺少 ${file}`);
  const target = path.join(root, file);
  await mkdir(path.dirname(target), { recursive: true });
  const existing = await stat(target).catch(() => null);
  const fileStarted = performance.now();
  const cached = existing?.size === info.size;
  if (existing?.size !== info.size) {
    console.log(`下载 ${file} (${(info.size / 1048576).toFixed(2)} MiB)`);
    let complete = false;
    for (let attempt = 0; attempt < 6 && !complete; attempt++) {
      const offset = (await stat(`${target}.partial`).catch(() => null))?.size ?? 0;
      if (offset === info.size) { complete = true; break; }
      try {
        const response = await fetch(`https://huggingface.co/${model}/resolve/${revision}/${file}`, { headers: offset ? { Range: `bytes=${offset}-` } : {}, signal: AbortSignal.timeout(180000) });
        if (!response.ok || !response.body) throw new Error(`${file}: HTTP ${response.status}`);
        const append = offset > 0 && response.status === 206;
        await pipeline(Readable.fromWeb(response.body), createWriteStream(`${target}.partial`, { flags: append ? 'a' : 'w' }));
        complete = (await stat(`${target}.partial`)).size === info.size;
      } catch (error) { console.log(`下载中断，正在续传 (${attempt + 1}/6)：${error.message}`); }
    }
    if ((await stat(`${target}.partial`)).size !== info.size) throw new Error(`${file}: 下载长度不符，请重试`);
    await rename(`${target}.partial`, target);
  }
  const { createReadStream } = await import('node:fs');
  const hash = createHash('sha256');
  for await (const chunk of createReadStream(target)) hash.update(chunk);
  const sha256 = hash.digest('hex');
  if (info.lfs?.sha256 && info.lfs.sha256 !== sha256) throw new Error(`${file}: SHA256 校验失败，请删除文件后重试`);
  manifest.files.push({ file, bytes: info.size, sha256, cached, downloadAndVerifyMs: performance.now() - fileStarted });
}
await writeFile(path.resolve('public/models/manifest.json'), JSON.stringify({ ...manifest, completedAt: new Date().toISOString(), elapsedMs: performance.now() - started }, null, 2));
console.log('已下载并校验。模型由浏览器本地加载；服务端不会执行模型。');
