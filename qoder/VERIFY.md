# 验证样例

这些步骤需要真实 PowerContext，不能用页面提示或健康接口的静态成功响应替代。

## 自动验证

在 `qoder/` 下完成 DEPLOY.md 的服务安装与配置，然后运行：

```powershell
npm ci
npm run build
npm test
npm run test:integration
```

集成测试会在配置的 PowerContext 中创建一个独立测试 Scope，不读取或删除其他 Scope。
使用配置的服务认证需要具备创建测试 Scope 的权限。
它验证任务创建、更新、Handoff prepare、交接记忆保存、应用重启恢复、Context Prepare 召回、版本冲突拒绝、软删除和错误路由。
结果写入被忽略的 `test-results/integration.json`，包含服务返回的测试 Scope、记忆 ID 和验证时间。
测试记录保留在专用 Scope，便于核对，不自动删除。

## 人工样例

初始状态：新建的专用 Scope，没有任务；页面显示“已连接 PowerContext”。

1. 新建任务，标题 `OceanReview 项目验收`，描述 `核对部署与记忆复用`，优先级高。
2. 编辑进度为 60%。预期：状态为“进行中”，仪表盘平均进度为 60%。
3. 创建工作交接，摘要 `已完成接口核对`，下一步 `继续核对部署`。
4. 在项目与验证页展开“保存凭据”，记录任务和交接的 ID 与版本。
5. 关闭页面，重启 Node 应用，换一个浏览器或无痕窗口。预期：同一任务 ID、60% 进度、交接摘要和下一步仍然存在。
6. 工作交接页检索 `OceanReview`。预期：显示服务返回的相关历史上下文，而不是浏览器自行拼接的结果。
7. 断开 PowerContext 后尝试新增任务。预期：显示连接错误，不提示保存成功；恢复服务后该失败任务不出现在列表中。
8. 在两个窗口打开同一版本任务，窗口 A 保存后，窗口 B 再保存。预期：窗口 B 报版本冲突，不能覆盖 A 的新进度。
9. 可选：停止 PowerContext，使用**相同 POWERCONTEXT_HOME**重新启动。预期：任务和交接仍可恢复。

## 直接核对服务端读写

从 `.env` 读取真实 `POWERCONTEXT_SCOPE_ID`，向 `/v1/atomic-memory/list` 发送：

```json
{"scope_id":"<服务返回的 Scope ID>","kind":"task-tracker.task.v1","states":["active"],"limit":100}
```

交接快照的 kind 是 `task-tracker.handoff.v1`。
结果中的 `artifact.artifact_id`、`artifact.revision` 应与页面一致。
任务 JSON 存在记忆 `text` 中；更新替换同一记忆的新版本；删除写入 `deleted: true`，保留历史。

工作交接先调用 `/v1/work/handoffs/prepare-current`，使用真实 `source_id` 和正确的 CurrentWorkHandoff 结构。
该接口返回的 Prepared Handoff 是临时载体，不是 committed Handoff。
本应用另外将交接快照和 boundary receipt 明确保存为 Atomic Memory，供后续会话读取。
`/v1/context/prepare` 用 `query` 查询已保存记忆，结果作为不可信历史文本展示，不作为执行指令。

## 发布验收

必须在 **Qoder Sites** 发布 `dist/` 和 `functions/`，配置服务端 Secrets，再用无痕窗口执行以上样例。
本地测试通过不代表生产环境 Secrets、网络访问和站点可见性已通过验证。
将这次验收的截图、脱敏日志和真实 Qoder 开发记录附在提交材料中；不要提交认证令牌。
