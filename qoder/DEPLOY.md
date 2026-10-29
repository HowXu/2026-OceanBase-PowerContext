# 运行与部署

以下命令从本仓库的 `qoder/` 目录运行。不要覆盖已有 `.env`，不要提交真实凭据。

## 1. 安装固定版本 PowerContext

需要 Python 3.11+、Git、uv。Windows 属于 PowerContext 的实验性支持平台；本项目已完成真实 Windows 本地集成测试。

```powershell
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements-powercontext.txt
```

Linux/macOS 将 Python 路径改为 `.venv/bin/python`。
依赖固定到官方提交 `bf2ac1fcd9223af7bbab97f8e8e39a269b07fee3`，测试服务版本为 `1.4.2.dev46+gbf2ac1fcd`。
基础显式记忆、全文召回和本项目的交接不要求额外模型 API Key。

## 2. 启动持久 PowerContext

PowerShell，在第一个终端：

```powershell
$env:POWERCONTEXT_HOME = Join-Path (Get-Location) '.data/powercontext'
$env:POWERCONTEXT_SERVER_HTTP_HOST = '127.0.0.1'
$env:POWERCONTEXT_SERVER_HTTP_PORT = '8000'
& ./.venv/Scripts/powercontext.exe server run --no-env-file
```

Linux/macOS：

```bash
POWERCONTEXT_HOME="$PWD/.data/powercontext" \
POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1 \
POWERCONTEXT_SERVER_HTTP_PORT=8000 \
.venv/bin/powercontext server run --no-env-file
```

保持进程运行，确认 `http://127.0.0.1:8000/health/ready` 返回可用状态。
所有重启必须使用相同的 `POWERCONTEXT_HOME`，其中保存持久 SQLite 数据。
如果 8000 已被占用，换端口，同时修改应用 `.env`。

## 3. 配置应用

第二个终端，Node.js 22.9+：

```powershell
npm ci
Copy-Item .env.example .env
# 编辑 .env 后再继续；如已存在 .env，不要执行上面的复制命令。
npm run setup:scope
npm start
```

`setup:scope` 从 `/v1/scopes` 取得真实 Scope ID 写回 `.env`，使用稳定 idempotency_key 避免重复创建。
如已有专门用于此应用的 Scope，填入其 ID 并跳过初始化。
默认网址 http://127.0.0.1:3000 ，`PORT` 可修改；`HOST` 默认仅本机。

| 变量 | 说明 |
| --- | --- |
| POWERCONTEXT_URL | 本地为本机服务地址；生产必须是 Qoder Function 能访问的 HTTPS 服务 |
| POWERCONTEXT_SCOPE_ID | PowerContext 返回的真实 ID，不能用任务名称代替 |
| POWERCONTEXT_TOKEN | 上游 Bearer token，只在服务端配置 |
| POWERCONTEXT_AUTH_HEADER | 可选完整认证头；非空时优先于 TOKEN |
| PORT / HOST | 仅用于本地 Express |

先运行 `npm test` 和 `npm run test:integration`，再按 VERIFY.md 完成人工样例。

## 4. 生产 PowerContext

目前没有公网服务时，可使用 [deploy/README.md](deploy/README.md) 中提供的 Docker、持久卷与 HTTPS 配置。
这些配置尚未部署到云服务器，也未创建任何付费资源。

Qoder 云端的 `127.0.0.1` 指向云端函数自身，无法访问你的电脑。
需要已有、可持久运行的 PowerContext HTTPS 服务：

1. 将固定版本服务部署到你有权限的服务器，持久挂载 POWERCONTEXT_HOME。
2. 使用 TLS 反向代理，配置 POWERCONTEXT_SERVER_ACCESS_MODE=enforced 和 POWERCONTEXT_SERVER_AUTH_TOKEN。
3. 把对应凭据配置到 Qoder Function 的 Secrets，不写入网页或 dist。
4. 用生产 URL、Scope 和认证运行健康读取、验证样例，再确认对评审可访问。

本仓库不附带云主机或凭据，也不自动把本机无认证服务暴露到公网。
官方部署参考：https://github.com/oceanbase/powercontext/blob/bf2ac1fcd9223af7bbab97f8e8e39a269b07fee3/docs/zh/docs/operate/deploy-server.md

## 5. 更新既有 Qoder Sites

```powershell
npm run build
```

产物：

- `dist/index.html`：单文件网站，默认调用同源 `/functions/v1/app`。
- `functions/index.ts`：Deno 函数入口。
- `functions/handler.js`：从 src 生成的自包含实现，上传 functions 目录即可。

在 Qoder 中打开本修复目录，定位原有“任务进度追踪器”站点，保留原站点关联。
通过 Qoder Sites 工作流将静态目录设为 `dist`、函数目录设为 `functions`，函数路由使用 `/functions/v1/app`。
将 POWERCONTEXT_URL、POWERCONTEXT_SCOPE_ID、POWERCONTEXT_TOKEN（或 AUTH_HEADER）配置为 Secrets。
先预览并完成 VERIFY.md 样例，再通过 Qoder 发布。网站应允许“任何人都可访问”。
发布后用无痕窗口确认首页、任务保存、刷新恢复、交接和召回均成功。
生产 `/functions/v1/app/health` 必须实际完成 Memory 读取；未配置时应报错，不能使用静态 healthy 响应。

不要手工编造 `.qoder.site` 中的发布编号。Qoder 应在真实发布完成后更新发布记录。
本仓库生成资产和 GitHub 提交不代表已有生产发布成功。
Qoder 操作参考：https://docs.qoder.com/zh/qoder/sites

## 6. 故障处理

- “尚未配置”：核对 Secrets 或 `.env`；修改后重启本地应用或重新部署函数。
- HTTP 401/403：核对上游认证与 Scope 权限，不把浏览器的 Qoder 登录令牌当成 PowerContext Token。
- HTTP 404：核对服务返回的 Scope ID、服务版本和路由。
- 版本冲突：刷新读取最新内容，再合并本次修改。
- 连接失败：保留表单输入，不视作已保存；网络超时后先刷新核对是否已有记录，再决定是否重试。
- 历史召回为空：用任务中的明确关键词测试；基础模式是全文检索，不声称具有未配置的语义检索能力。
