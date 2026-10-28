# 验收状态与证据索引

完成日期：2026-10-28（Asia/Shanghai）。

**题面六项必做项已在本地验证通过，真实 MiniMax 开发记录、源码、说明与验证材料齐备。** 主验收命令为 `python scripts/demo.py --mode recorded --run-dir /tmp/sdd-review`，从空运行数据开始，不需要用户的密钥。

| 必做项 | 验证结果与证据 |
| --- | --- |
| 能够运行 | 固定依赖、初始化与 CLI 入口见 README；完整流程从全新目录执行，交付修改前快照和重置方法 |
| MiniMax 实际参与 | `evidence/live/minimax/request.json` 保存实际任务和检索上下文；`response.json` 保存 MiniMax-M3.1-Flash 实际代码响应、响应标识、HTTP 200、用量和耗时；响应代码与交付业务源码一致 |
| 上下文正确 | `evidence/live/05-restarted-context.json` 包含必要有效规格、代码和测试；退役条目被排除；`13-updated-spec-context.json` 使用重新导入的 v2 内容 |
| 数据库实际参与 | `evidence/live/technology-events.jsonl` 记录真实 seekdb 资料导入与 SQL 查询，返回来源直接用于任务上下文 |
| 状态能够复用 | 同一事件记录中，PowerContext 写入与后续独立进程读取具有相同准确引用；恢复状态实际进入后续上下文；`10-restarted-verified-context.json` 和 `15-final-restarted-context.json` 保留验证结果 |
| 代码修改有效 | `evidence/live/before-target.log`：目标测试 20 失败、1 通过；`after-target.log`：同一组 21 项全部通过；8 项回归始终通过；前后测试 SHA-256 一致，代码差异见 `code.diff` |

真实调用模型为 `MiniMax-M3.1-Flash`，响应标识为 `0730cb156aefc52151c3e8e636fbd175`。模型输入 7086 tokens，输出 412 tokens；本次请求耗时 2.267 秒。只保存可见代码响应和用量等元数据，不保存密钥、认证头或私有思维链。

`sample/catalog/service.py` 已替换为模型实际返回的实现，包括 `_validate_min_stock` 参数校验和包含边界的库存筛选。修改前代码保存在 `fixtures/before/service.py`，前后快照保存在 `evidence/live/service-before.py` 与 `service-after.py`。目标测试、回归测试和必要规格来源在 [VALIDATION.md](VALIDATION.md) 列明。

模块测试另有 12 项通过，记录在 `evidence/live/module-tests.json` 和 `module-tests.log`。验证绑定资料快照和任务哈希，规格更新后旧结果会过期，再次验证后才恢复为当前有效。

独立干净环境复现也已通过，记录在 `evidence/replay-clean/`：从 `c947d6240d4744a9bd63d8aa790ceb33d41c0b35` 的源码归档建立新目录和新虚拟环境，按锁定依赖安装，清除模型密钥与代理环境变量，使用同一真实模型响应完成全部步骤。源码目录不含 `.env`，运行时数据库和任务状态均从空开始，没有再次调用模型。

`evidence/reference/` 与 `evidence/clean/` 是开发期间的参考实现验证，明确标记 `minimax_actual_participation=false`，保留用于比较。正式开发证明以 `evidence/live/` 为准，重新验收以 `recorded` 流程的实际执行结果为准。

作品分支为 `codex/minimax-sdd-context`，基于主办方 `d647275c308e09b7b623f322a26f5c4e85734fb0`，所有作品文件在 `minimax/`。代码和材料已准备为固定提交；比赛平台的最终提交尚未执行。

