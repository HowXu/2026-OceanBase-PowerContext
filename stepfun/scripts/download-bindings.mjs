// Optional resumable prefetch of the exact vendor artifact used by seekdb 1.4.0.
import { mkdir, stat, readFile, rename } from 'node:fs/promises';
import { createWriteStream } from 'node:fs';
import { once } from 'node:events';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import path from 'node:path';
const execute = promisify(execFile);
const curl = process.platform === 'win32' ? 'curl.exe' : 'curl';
const revision = '469a55dc84ea7a9e077161a8b454871a9adaff35';
const platform = `${process.platform}-${process.arch}`;
if (!['win32-x64', 'linux-x64', 'linux-arm64', 'darwin-arm64'].includes(platform)) throw new Error(`不支持 ${platform}，请使用本地 server 模式`);
const filename = `seekdb-js-bindings-${platform}.zip`;
const url = `https://oceanbase-seekdb-builds.s3.ap-southeast-1.amazonaws.com/js-bindings/all_commits/${revision}/${filename}`;
const folder = path.resolve('.data/bindings', revision, platform);
await mkdir(folder, { recursive: true });
const head = await execute(curl, ['-fsSI', '--retry', '2', '--max-time', '30', url]);
const size = Number(head.stdout.match(/content-length:\s*(\d+)/i)?.[1]);
if (!size) throw new Error('无法读取官方组件大小');
const target = path.join(folder, filename);
if ((await stat(target).catch(() => null))?.size === size) { console.log('组件缓存已存在'); process.exit(0); }
const partSize = 4 * 1024 * 1024;
const parts = Array.from({ length: Math.ceil(size / partSize) }, (_, i) => ({ i, start: i * partSize, end: Math.min((i + 1) * partSize, size) - 1 }));
let next = 0;
await Promise.all(Array.from({ length: 4 }, async () => {
  while (next < parts.length) {
    const part = parts[next++];
    const file = path.join(folder, `${filename}.part${part.i}`);
    if ((await stat(file).catch(() => null))?.size === part.end - part.start + 1) continue;
    for (let attempt = 0; attempt < 4; attempt++) {
      try {
        await execute(curl, ['-fsSL', '--retry', '2', '--max-time', '90', '--range', `${part.start}-${part.end}`, '-o', file, url]);
        if ((await stat(file)).size !== part.end - part.start + 1) throw new Error('分段长度错误');
        console.log(`seekdb 组件分段 ${part.i + 1}/${parts.length} 已缓存`); break;
      } catch (error) { if (attempt === 3) throw error; console.log(`重试分段 ${part.i + 1}`); }
    }
  }
}));
const output = createWriteStream(`${target}.partial`);
for (const part of parts) { if (!output.write(await readFile(path.join(folder, `${filename}.part${part.i}`)))) await once(output, 'drain'); }
output.end(); await once(output, 'finish');
if ((await stat(`${target}.partial`)).size !== size) throw new Error('组件总长度错误');
await rename(`${target}.partial`, target);
console.log('官方 seekdb 组件已缓存。SDK 下次启动将解压加载。');
