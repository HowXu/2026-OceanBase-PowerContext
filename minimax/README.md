# 规有所依：SDD 工程上下文模块

根据开发任务，从 **seekdb 的实际查询结果**中取出当前有效规格、代码、测试和样例数据，结合 **PowerContext 的持久任务状态**构造上下文；通过 MiniMax HTTP 接口生成受限文件修改，并保存可核对的开发及验证记录。

**已完成真实 MiniMax 开发与本地验收。** 交付记录使用 `MiniMax-M3.1-Flash` 实际生成代码，目标 21 项、回归 8 项、模块 12 项测试通过。评审使用下方 `recorded` 命令即可从空数据库重现结果，**无需密钥，也不会再次调用模型**。六项必做项的证据索引见 [SUBMISSION_STATUS.md](SUBMISSION_STATUS.md)。

## 1. 环境与安装

已在 Windows 11 的 WSL Ubuntu、Linux x86_64、Python 3.11.15 上实测。运行模块需要 Linux；Windows 请在 WSL 中执行。数据库是 `pylibseekdb` 启动的本地 seekdb 运行时，无需另装数据库服务或 Docker。PowerContext 使用自己的 SQLite 持久化存储；工程资料的检索来自 seekdb。

固定版本：PowerContext 1.4.0、pylibseekdb 1.4.0.post1、pytest 8.4.2、httpx 0.28.1。完整依赖版本在 `requirements.lock`。显式补充 `pycasbin==2.8.0`，用于解决当前 PowerContext 1.4.0 的 builtin 导入对 `casbin` 的依赖。

从本分支仓库根目录进入 `minimax/`，使用已安装的 [uv](https://docs.astral.sh/uv/)：

```bash
cd minimax
uv venv --python 3.11 .venv
uv pip install --python .venv/bin/python -r requirements.lock
uv pip install --python .venv/bin/python --no-deps -e .
source .venv/bin/activate
```

也可以用 Python 3.11 的 `venv` 和 `pip` 安装同一份锁定依赖。Git 用于记录代码基线；首次安装需要访问 PyPI，此后重放已交付记录不需要模型或网络。

## 2. 一条命令重现完整验收流程

以下命令创建全新运行目录，不覆盖已有项目或任务状态。目录必须尚不存在；再次运行请换一个目录名。

```bash
python scripts/demo.py --mode recorded --run-dir /tmp/sdd-recorded-demo
```

该模式核对已交付的 `evidence/live/minimax/response.json`，将其中真实 MiniMax 代码响应应用到修改前工程，并重新运行全部流程。会验证导入、数据库检索、修改前后测试、跨进程恢复、规格更新、旧验证失效和重新验证。不会把重放记录伪装成一次新的模型调用。

输出在 `/tmp/sdd-recorded-demo/evidence/`：

- `before-target.log`：同一组 21 项目标测试在修改前 20 失败、1 通过，失败是断言或未抛出 ValueError，回归 8 项通过。
- `after-target.log`、`after-regression.log`：修改后目标 21 项和回归 8 项通过。
- `code.diff`、`service-before.py`、`service-after.py`：代码快照和差异。
- `05-restarted-context.json`、`10-restarted-verified-context.json`：新进程读取并使用先前保存的状态及验证结果。
- `13-updated-spec-context.json`：规格 v2 生效，旧验证标为过期；`15-final-restarted-context.json` 是重新验证后的状态。
- `technology-events.jsonl`：seekdb 写入和查询、PowerContext 写入与读取记录，包含不同进程 PID 和准确记录引用。
- `commands.json`、`before.json`、`after.json`、`spec-v2.json`：命令、退出码、Git 基线、代码文件 SHA-256、测试文件 SHA-256。
- `acceptance.json`：明确标明该运行是否实际使用 MiniMax。

已交付的 `evidence/live/` 保留真实开发过程：`minimax/request.json` 是提供给模型的任务和完整上下文，`minimax/response.json` 是实际代码响应及模型标识、用量和耗时。`sample/catalog/service.py` 与该响应中的代码逐字一致；测试文件在修改前后保持同一 SHA-256。

模块自身的测试：

```bash
python scripts/check.py --evidence-dir /tmp/sdd-module-checks
```

## 3. MiniMax 实际开发与记录

API Key 是允许程序调用 MiniMax 的接口密钥。它需要在 [MiniMax 开放平台](https://platform.minimax.cn/)创建，账号须有可用额度。网页聊天账号或其他模型订阅不能自动替代这个接口密钥。

复制 `.env.example` 为 `.env`，在本机填入自己的值：

```dotenv
MINIMAX_API_KEY=YOUR_REAL_KEY
MINIMAX_BASE_URL=https://api.minimax.cn/anthropic
MINIMAX_MODEL=MiniMax-M3.1-Flash
```

`.env` 已被忽略，不要提交到仓库。模型名、地区入口可按自己的可用账户调整。程序使用 Anthropic 兼容 HTTP `/v1/messages`；模型输出只允许修改任务声明的代码文件，不能写测试或规格。

```bash
python scripts/demo.py --mode live --env-file .env --run-dir /tmp/sdd-minimax-live
```

成功后，`evidence/minimax/request.json` 保存实际任务和输入上下文，`response.json` 保存模型及请求标识、用量、耗时、代码响应、上下文快照及请求哈希。不会保存认证请求头或模型私有思维链。

如需重新开展一次真实模型开发，成功后将运行目录中的 `evidence/` 内容保存到自己的交付目录。已交付的这一次开发记录，可在没有密钥的干净环境中重放：

```bash
python scripts/demo.py --mode recorded --run-dir /tmp/sdd-recorded-demo
```

`recorded` 会核对记录来自 MiniMax、HTTP 成功以及源快照一致；不会再次调用模型。它不假装产生一次新模型调用。如果缺少真实记录，会直接失败，不会悄悄改用参考补丁。评审不需要让模型再次生成相同补丁。

如果 WSL 访问模型接口超时，可为本次进程配置可用的 `HTTPS_PROXY` / `HTTP_PROXY` 后重试；代理地址由自己的网络环境确定。已交付记录的离线重放不需要代理或密钥。

用于比较的 Codex 参考实现也予以保留，可运行 `python scripts/demo.py --mode reference --run-dir /tmp/sdd-reference-demo`。该模式明确标记为 `Codex-reference-NOT-MiniMax`，不计入 MiniMax 使用记录；主验收入口始终是上方的 `recorded` 模式。

## 4. CLI 输入和输出

默认任务为 `sample/task.json`，其所在目录即业务项目根目录。任务声明 objective、scope、manifest、可改文件、必要来源、目标测试和回归测试。manifest 声明资料的路径、类型和工程作用域。

```bash
# 显式导入实际文件
sdd-context --state-dir /tmp/sdd-manual ingest

# 检索当前上下文，JSON 输出写到指定文件
sdd-context --state-dir /tmp/sdd-manual --output /tmp/context.json context

# 保存任务进度，之后的 CLI 命令是独立新进程
sdd-context --state-dir /tmp/sdd-manual checkpoint --done retrieve-current-context --note "已完成规格核对"
sdd-context --state-dir /tmp/sdd-manual --output /tmp/resumed.json context

# 真实 MiniMax 修改；修改后需要重新导入代码
sdd-context --state-dir /tmp/sdd-manual agent --env-file .env --evidence-dir /tmp/manual-minimax
sdd-context --state-dir /tmp/sdd-manual ingest
sdd-context --state-dir /tmp/sdd-manual verify --evidence-dir /tmp/manual-checks
```

自定义项目可通过 `--task /absolute/project/task.json` 接入。不同项目应使用不同 `project_id`；不同任务应使用不同 `id`。PowerContext 的任务作用域由项目和任务共同决定。

`context` 返回：

| 字段 | 含义 |
| --- | --- |
| `task` | 当前任务及验收入口 |
| `snapshot`、`files` | seekdb 当前导入快照、实际源文件哈希 |
| `sources` | 数据库返回的规格、代码、测试、数据，含文件路径、符号、真实行号、原文 |
| `progress.completed`、`progress.todo` | PowerContext 恢复的完成项和待办 |
| `progress.verification` | 已有验证的结果及适用快照 |
| `progress.verification_current` | 验证是否适用于当前资料和任务；通过与否需另看 `verification.passed` |
| `progress.powercontext_reference` | 本次读取的准确持久记录引用 |

退出码：0 成功，1 验证测试未全部通过，2 输入或资料新鲜度错误。`--output` 是推荐的机器读取方式。

## 5. 验证样例和重置

完整样例见 [VALIDATION.md](VALIDATION.md)，一页技术说明见 [TECHNICAL_NOTE.md](TECHNICAL_NOTE.md)。样例业务可单独运行：

```bash
cd sample
python -m catalog --min-stock 5
```

预期返回 B、C、E，保持输入顺序。`--query ocean --min-stock 5` 仅返回 C。

重置建议直接为 `scripts/demo.py` 指定一个新的运行目录；脚本会从交付的 `fixtures/before/service.py`、样例数据、规格和同一份测试重新建立项目，不依赖之前的本地数据库。运行目录中的 `state/` 保存 seekdb 和 PowerContext 数据，可自行保留或清理。

## 6. 来源和限制

- 本仓库分支基于主办方 [2026-competition-ecosystem](https://github.com/oceanbase/powercontext/tree/2026-competition-ecosystem) 的 `d647275c308e09b7b623f322a26f5c4e85734fb0`。所有作品资产均在 `minimax/`。
- [题目原文](https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/qlqnoryzrs7gtqbw)；[PowerContext](https://github.com/oceanbase/powercontext/tree/powercontext-v1.4.0)；[seekdb bindings](https://github.com/oceanbase/seekdb-bindings)；[MiniMax HTTP 格式](https://platform.minimax.cn/docs/api-reference/text-anthropic-api)。这些组件按各自许可证使用；本作品的样例、模块和测试为新编写。
- 当前选择方式是 manifest 作用域与规格状态的 SQL 过滤，适合局部任务；未声称实现语义向量检索、自动需求理解或完整 Agent 框架。
- Python 资料索引定位到顶层函数、类和完整模块；跨语言工程需添加解析器和清单。上下文超出预算会报错，不会默默删掉必要约束。
- 同一状态目录通过进程文件锁串行访问。导入发布不可变快照，再切换当前指针；旧快照保留以供核对，尚未提供历史清理工具。
- 规格或代码变更必须显式重新导入；新快照或任务内容改变会使已有验证过期。系统不把历史状态、文档文字或模型响应当作授权。
- 实际模型输出可能不满足测试；任何一次失败都应保留日志并修复，不能修改或跳过目标测试来制造通过。当前示例采用一次受限编辑调用，失败时明确返回，由开发者重试或调整实现。

