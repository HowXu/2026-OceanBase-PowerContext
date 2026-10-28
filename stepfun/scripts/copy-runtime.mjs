import { mkdir, readdir, copyFile } from 'node:fs/promises';
import path from 'node:path';
const source = path.resolve('node_modules/onnxruntime-web/dist');
await mkdir('public/ort', { recursive: true });
for (const name of await readdir(source)) {
  if (/^ort-wasm.*\.(wasm|mjs)$/.test(name)) await copyFile(path.join(source, name), path.resolve('public/ort', name));
}
console.log('ONNX Runtime Web assets ready (same-origin, offline).');
