# PowerContext 任务进度追踪器

面向工作提效：保存任务进度、工作摘要和下一步，让不同会话能够继续工作。

## 核心流程

1. 任务保存为 PowerContext Atomic Memory，服务返回实际记忆 ID 和版本后才提示成功。
2. 每次打开、刷新页面都从 PowerContext 重新读取任务与交接；Node 重启或更换浏览器不丢失。
3. 编辑使用服务返回的 ETag 和版本校验，旧会话不能覆盖新进度。
4. 工作交接调用正确的 Handoff prepare 接口，随后把可复用快照单独保存为 Atomic Memory。
5. 通过 Context Prepare 按关键词召回历史，在交接页展示服务返回的内容。

不依赖浏览器本地存储提供持久化；PowerContext 不可用时操作明确失败，不静默降级。
可显式导入旧版浏览器任务，原始本地数据保留。

## 快速开始

环境：Node.js 22.9+、Python 3.11+、Git、uv。已在 Node 24.14.1、Python 3.12.12 验证。

先按 [DEPLOY.md](DEPLOY.md) 安装并运行固定版本的 PowerContext。在本目录执行：

```powershell
npm ci
Copy-Item .env.example .env
# 编辑 .env，配置真实的 PowerContext 地址和认证。
npm run setup:scope
npm start
```

打开 http://127.0.0.1:3000 。`setup:scope` 创建应用专用 Scope 并将服务返回的真实 ID 写入 `.env`。
如果已有专用 Scope，直接填入其 ID，跳过该命令。
启动脚本会加载 `.env`；进程环境变量优先。

## 验证与构建

```powershell
npm run build
npm test
npm run test:integration
```

集成测试必须连接真实 PowerContext，并创建独立测试 Scope；不会用模拟服务代替。
人工步骤、预期结果和服务端核对方式见 [VERIFY.md](VERIFY.md)。

## Qoder Sites

`npm run build` 从同一份源码生成单文件 `dist/index.html` 和自包含的 `functions/handler.js`。
Node 使用 `/api`，Qoder 使用 `/functions/v1/app`，两端业务实现相同。
在 Qoder Sites 发布这两个目录，并配置生产 Secrets；详细流程见 [DEPLOY.md](DEPLOY.md)。

当前站点：https://task-tracker-ze8ndto7cy0.qoder.zone/

GitHub 推送不等于站点发布。现有 `.qoder.site` 是原站点的关联及旧发布快照；本地构建不会伪造发布记录。
开发来源、待补交的 Qoder 记录和提交清单见 [SUBMISSION.md](SUBMISSION.md)。

## API

| 接口 | 用途 |
| --- | --- |
| GET /api/health | 实际读取 PowerContext Memory，验证配置、网络与 Scope 权限 |
| GET /api/tasks | 从 PowerContext 读取任务 |
| POST /api/tasks | 创建任务，返回真实记忆 ID 和 revision |
| PUT /api/tasks/:id | 更新任务，必须包含当前 revision |
| DELETE /api/tasks/:id | 软删除，JSON 请求体必须包含当前 revision |
| GET /api/handoff | 读取已保存交接 |
| POST /api/handoff | 准备交接并保存记忆快照 |
| POST /api/context | 以 query 读取相关历史上下文 |

任务 ID 为不透明字符串。进度为 0–100 整数，状态由进度推导。
交接快照保留创建时的任务版本；删除任务不抹去交接历史。
上游认证只保存在服务器环境中，浏览器不能选择 Scope 或上游地址。
当前为部署者指定的共享演示工作区；只放演示数据，多用户私有部署需要另外配置身份和权限边界。

## 目录

```text
src/         PowerContext 客户端、业务逻辑、共享 Fetch API 与 Express 入口
public/      本地与站点共用的页面、样式与交互源码
scripts/     构建与真实 Scope 初始化
dist/        生成的 Qoder Sites 单文件页面
functions/   生成的 Qoder Function 与 Deno 入口
test/        失败处理测试、真实 PowerContext 集成测试
```

PowerContext 为 OceanBase 开源项目，接口实现参考固定版本的官方 OpenAPI 和 API quickstart。
原项目及本轮修复来源见 SUBMISSION.md；不把 Codex 修复冒充为 Qoder 开发证明。

许可证：Apache-2.0（见 LICENSE）。
