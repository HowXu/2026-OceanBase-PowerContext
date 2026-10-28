# 拾知：端侧 AI 推理与 seekdb 混合检索

比赛题目：[端侧 AI 推理性能优化与 seekdb 混合检索](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/kw9wfx4bu9pyvat9)。实现基于 `2026-competition-ecosystem`，所有项目资产位于 `stepfun/`。

文档导入 → 浏览器 Worker 本地生成 embedding → seekdb-js 写入 → 显式索引刷新 → 浏览器本地查询向量 → seekdb 数据库内全文与向量融合 → 展示带来源的原文。

## 快速启动

需要 Node.js **22.12+**（验证环境为 Node 24）、Docker、Chrome / Edge 新版，建议 8 GB 可用内存。WebGPU 需浏览器与 GPU 支持；无 WebGPU 自动降级 WASM。使用 `http://127.0.0.1:3000`，localhost 是安全上下文，不需要 HTTPS 证书。

```sh
cd stepfun
npm ci
npm run models:download
docker compose up -d --wait
npm run dev
```

打开 **http://127.0.0.1:3000**。默认连接本机 seekdb server，启动完成后左下角显示“seekdb 已连接”。没有云端推理、服务端推理或预生成向量。模型下载脚本仅下载公开文件并验证 SHA256；数据库启动不加载 embedding 模型。

生产方式：

```sh
npm run build
npm start
```

`npm ci` 使用锁文件；`.npmrc` 禁止自动安装 seekdb 的默认服务端 embedding peer（该 peer 包含不适用于独立 npm 项目的 `workspace:*` 依赖）。模型与 ORT 运行时全部从同源本地静态文件加载，下载完成后应用运行不需要外网。依赖覆盖锁定已修复版本的 `sharp` / `adm-zip`。

### 数据库配置与嵌入式备选

推荐使用提供的 Docker Compose。仅开放本机回环端口，数据保存到 Docker 命名卷（兼容 Windows）：

```sh
docker compose up -d --wait
```

无需 `.env` 即可连接 `127.0.0.1:2881`。如需自定义连接，复制 `.env.example` 为 `.env`。固定镜像 `oceanbase/seekdb:1.4.0-100000172026082615`，数据卷目录 `/var/lib/seekdb`。启动较慢时应用会自动重试连接。

也支持 `SEEKDB_MODE=embedded`，使用 seekdb-js 官方原生组件。首次从官方 S3 下载当前平台组件，缓存和数据库均保存在 `.data/`。可先运行 `npm run db:download`（使用系统 curl，支持分段重试），再启动应用。**本次 Windows 实测官方嵌入式组件启动停滞，故本次验收与性能数据使用本机 Docker server；不声称嵌入式路径已验证通过。** Windows 可使用 Docker Desktop 或已安装 Docker 的 WSL；后者在 stepfun 目录执行 `wsl -d <发行版> -u root -- docker compose up -d --wait`。

```sh
npm run db:check
```

可单独验证 seekdb 连接、版本与已有集合。**嵌入式模式下，先停止应用再运行该命令**，避免多个进程同时打开相同数据目录。

## 演示与验收

1. 新建知识库，默认 FP16 优先使用 WebGPU，也可选择 INT8。也可直接点击“导入 20 篇示例资料”，自动创建默认知识库。
2. 查看“模型加载 → 文档向量化 → 写入 → 索引刷新”进度。等待“索引已刷新”后查询“没有可用 GPU 还能计算向量吗？”。
3. 在混合检索与语义检索间切换，查看数据库返回顺序、来源、片段序号和完整文本。
4. 再导入 UTF-8 `.txt` / `.md` 或粘贴文本。相同来源、标题和正文的重复导入使用相同 SHA256 ID，upsert 不会重复增加记录。正文修改按新版本追加保存。
5. 新建另一个知识库，导入不同文本，切换知识库验证隔离。刷新页面后保留选中集合，服务端从 seekdb 恢复集合和计数；旧查询结果不会跨集合显示。
6. 在“本地推理设置”选择 WASM，重新加载模型，再完成导入与检索以验证降级路径。自动模式会先检查适配器并真实运行模型预热；初始化、算子或后续设备故障会降级 WASM，并提示原因。显式“仅 WebGPU”模式失败时直接报错，用于评测记录，避免把 CPU 数字误记成 GPU。
7. 打开“性能与质量”，运行完整评测并导出 JSON。不可用的 GPU 配置显示跳过原因，不编造数值。

## 模型与量化复现

原始模型：[BAAI/bge-small-zh-v1.5](https://huggingface.co/BAAI/bge-small-zh-v1.5)（MIT）。ONNX 文件来源：[Xenova/bge-small-zh-v1.5](https://huggingface.co/Xenova/bge-small-zh-v1.5)，固定 revision `75c43b069aac4d136ba6bc1122f995fedcfd2781`。运行时 Transformers.js 3.8.1；512 维 CLS pooling + L2 归一化；查询增加模型规定的中文检索前缀，文档不加。

本应用直接使用上述版本公开提供的 `onnx/model_quantized.onnx`（INT8 动态量化，发布者配置 `per_channel=true / reduce_range=true / QInt8`）和 `onnx/model_fp16.onnx`（半精度），基线为同版本 `onnx/model.onnx`（FP32）。**没有训练、重新量化或改变词表**。下载脚本固定 revision，校验 Hub LFS SHA256，将每个文件的大小、SHA256 与下载时间写入 `public/models/manifest.json`。比较模型体积时只统计对应 ONNX 权重与图，分词器/配置是三者共用文件，不混入某一精度；JSON 同时保留所有文件以便核对口径。

模型下载失败可重新运行，`.partial` 文件支持 HTTP Range 续传。公开模型不需要 API key。若网络限制访问 Hugging Face，可在可访问网络上执行相同脚本，将完整 `public/models/` 拷回本机，保持目录结构和 manifest。禁止用其他版本文件替换而不更新 `shared/model.ts` 的 revision 与向量空间标识。

## 存储、刷新与会话

- `stepfun_registry` 在 seekdb 中持久化集合名称、显示名称、精度和完整向量空间标识。
- 每个知识库一个随机 `kb_<24 hex>` 集合。seekdb-js 创建 `id / document / embedding / metadata` 表、512 维 cosine HNSW 索引和 `ngram(2)` 全文索引；显式 `embeddingFunction: null` 关闭服务端默认模型。
- 片段 metadata 保存 `source / sourceId / chunk / total`。350 Unicode 字符分块、50 字符重叠、优先句尾截断。极端 tokenizer 输入仍由模型的最大长度约束保护。
- 每次最多提交 32 片段，每个推理批默认 8 条；导入完成统一 `CALL dbms_index_manager.refresh()`，只有成功才显示可检索。失败后保留已写入数据，可重试同批导入或点击“刷新当前知识库索引”，不会伪报成功。
- 混合检索仅调用 `collection.hybridSearch({ query: { whereDocument: { $contains: ... } }, knn: { queryEmbeddings: ... }, rank: { rrf: ... } })`。SDK 通过 `DBMS_HYBRID_SEARCH.GET_SQL` 构造数据库内排序，服务端与浏览器都不融合两路结果。
- SDK 混合检索使用会话变量，单实例 API 将完整操作串行执行，防止并发请求覆盖 `@search_parm`，也防止写入、刷新与读取交错；这是本机个人知识库的明确取舍。
- UI 在任务期间锁定集合与模型选项，操作捕获集合 ID；后端检查空间标识、512 维、有限数值、L2 归一化与负载限制。该应用是单用户本机工具，绑定 `127.0.0.1` 并拒绝跨源请求，不提供多租户身份认证。

## 评测与原始数据

测试数据位于 `data/evaluation.json`，20 篇中文资料、10 个独立人工相关性标注查询。它是用于回归的透明小型样例，不能代表通用中文检索基准；无预存向量或预存检索结果。所有向量都由本次打开的浏览器计算。

评测配置包含 FP32/WASM/b1、INT8/WASM/b1、INT8/WASM/b8、INT8/WASM/b8 逐批刷新、FP32/WebGPU/b8、INT8/WebGPU/b8、FP16/WebGPU/b8。分别控制精度、推理批大小与刷新策略；后端比较需使用同精度配置。每个配置独立集合，预热后重复 3 轮，分别执行向量与混合检索；质量按 `sourceId` 去重计算 Recall@5 / nDCG@5。JSON 保留每个查询的结果 ID、标注、时间、各轮导入计时、模型初始化、首次预热、环境信息与中位数。第 0 轮为首次写入，第 1/2 轮为重复 upsert，原始数据明确保留，评估导入时需分别审视。

模型文件下载与离线本地加载是不同阶段：前者在下载脚本中，后者在浏览器 `pipeline()` 中，预热单独计时。操作记录的查询端到端计时覆盖加载（若发生）、向量化、HTTP、数据库、DOM 更新及下一帧；评测循环的端到端计时覆盖查询向量化、HTTP 与数据库响应，**不含结果绘制**。两种口径不直接混比。首次/后续加载是否命中缓存需结合原始记录解释，不能将缓存加载时间声称为冷网络下载。

## 开发验证

```sh
npm test
npm run build
```

测试覆盖 Unicode 分块全文还原、重复导入 ID、检索指标、非法向量、集合名校验，以及数据库会话串行化与错误恢复。真实端到端功能和性能通过应用内评测验证，详见 `docs/VALIDATION.md`。一页设计与实验说明见 `docs/TECHNICAL_NOTE.md`。

## 文件索引

`src/embedding.worker.ts` 本地模型与降级；`src/main.ts` 操作界面；`src/benchmark.ts` 真实评测；`server/db.ts` seekdb 数据层；`shared/` 模型与 API 契约；`scripts/` 模型与运行时准备；`data/` 固定评测集；`compose.yaml` 可选本地数据库服务。依赖、模型与本机数据库文件不提交 Git。

### GPU 数值校验与真实集成测试

每次新加载 WebGPU 模型，会将两条实际文本探针的输出与浏览器 FP32/WASM 参考结果比较，最小余弦须达到 0.98。失败时自动模式切换到同精度 WASM，严格 GPU 模式报错并在评测中记录跳过。本机 INT8/WebGPU 检查失败，FP16/WebGPU 通过；因此默认新知识库使用 FP16，INT8 可用于 WASM。参考模型也在浏览器运行；数值校验开销记录在 `validationMs`，包括失败尝试的总初始化开销记录在 `initMs`。该检查只用于数值兼容性，不替代完整 Recall/nDCG 评测。

浏览器导入示例后执行 `npm run test:db` 可复现数据库幂等写入、隔离、刷新、模型空间检查和断线恢复；仅删除本次测试自己创建的临时集合。性能、模型量化和降级的真实结果见 `docs/VALIDATION.md` 及 `docs/evidence/`。
