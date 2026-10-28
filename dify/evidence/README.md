# 实际验收记录

执行时间：2026-10-28T20:59:15+08:00（北京时间）。Dify 1.9.1，PowerContext 1.4.1。

以下结果来自真实 Docker 部署，所有六次操作都由配套网页 API 调用 Dify `/v1/workflows/run`，实际执行导入的 `station7.yml`。没有用本地 Python 执行器替代 Dify。

| 步骤 | 输入 | 回合 | 信任 | 存档修订 | Dify 节点 |
| --- | --- | --- | --- | --- | --- |
| 1 | 我用「会下雨的伞」交换车票。 | 1 | 1 | @1 | 9 / 9 成功 |
| 2 | 我用「会下雨的伞」交换车票。 | 1 | 1 | @1 | 9 / 9 成功 |
| 3 | 你还记得我上次带来的失物吗？ | 2 | 1 | @2 | 9 / 9 成功 |
| 4 | 你还记得我上次带来的失物吗？ | 1 | 0 | @1 | 9 / 9 成功 |
| 5 | 我把失物归还给原来的主人。 | 3 | 3 | @3 | 9 / 9 成功 |
| 6 | 站长，我下次还会回来。 | 4 | 3 | @4 | 9 / 9 成功 |

- 步骤 2 重放与步骤 1 完全相同的请求，回合与存档修订保持不变。
- 步骤 3 使用新 HTTP 客户端，不传旧对话，只传同一个存档口令；回答仍提到“会下雨的伞”，且没有新增物品或信任。
- 步骤 4 使用另一个存档口令，没有拿到前一个玩家的失物。
- 步骤 5 解锁“替月亮保管失物的人”；步骤 6 保留同一结局。
- 共 6 次实际 Dify 工作流、54 个成功节点；最后实际 PowerContext Artifact 为第 4 次修订。已直接读回正文核对回合、藏品和结局。
- 纯规则测试 6 项通过；JSON 转义、非法输入、重复物品、信任下限、终态和保存失败均有覆盖。
- 本次验收 **没有配置有效的模型密钥**，旁白均明确标注为 `rules`。模型叙事已实现，待用户填写有效 `MODEL_*` 配置后另外验证，不把规则旁白当作模型输出。

## 原始证据

- [六次输入及实际输出](dify-end-to-end.json)：完整 `workflow_run_id`、`memory_ref`、状态和旁白。
- [54 个节点的真实执行记录](dify-node-executions.json)：Dify Console API 读回的节点名称、类型、状态和耗时；特意没有导出请求体、HTTP 头或环境变量，避免包含密钥。
- [真实 PowerContext Artifact](powercontext-artifact.json)：通过 PowerContext HTTP 接口读回 Scope、修订、ETag 和存档正文。
- [容器替换后的持久性验证](restart-check.json)：替换 PowerContext 和网关容器后，Scope、Artifact、ETag、修订号、完整状态均保持一致。
- [实际 Python 依赖版本](python-packages.txt)：容器内导出的依赖清单，不含配置和密钥。
- [单独的后端接口验证](backend-integration.json)：仅用于后端验证，不代替上面的真实 Dify 执行。

复现：按上级 README 从空环境启动，运行 `python scripts/verify_dify.py`。脚本每次使用新的随机存档。若使用自动配置的本地 Dify，可再运行 `python scripts/collect_node_evidence.py` 导出节点摘要。
