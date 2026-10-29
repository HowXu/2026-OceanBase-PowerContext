# 提交材料与证据状态

## 项目

- 方向：工作提效；任务进度、跨会话工作交接与上下文召回。
- 指定技术：PowerContext。任务和交接快照保存为 Atomic Memory，打开应用时重新读取；Context Prepare 用于历史召回。
- 仓库：`HowXu/2026-OceanBase-PowerContext`，基于 `2026-competition-ecosystem`，资产在 `qoder/`。
- 现有 Qoder Sites 地址：https://task-tracker-ze8ndto7cy0.qoder.zone/

## 开发来源

原始项目随 `.任务进度追踪器.qoder.site` 保存了 Qoder 站点标识。
该文件不是“主要开发工作在 Qoder 中完成”的完整证明。
本轮功能修复由 Codex 完成，不能作为 Qoder 开发记录提交。
不得伪造开发对话、账号 UID、用户反馈或访问统计。

参赛者仍需提供：

- 关键 Qoder 需求拆解、编码、调试过程的真实截图或录屏。
- 真实 Qoder 账号 UID，核对账号与现有站点归属。
- Qoder 中审阅、调试、验证及发布修复版的记录。
- 如原项目主要开发不在 Qoder 中完成，应如实说明，并在 Qoder 中完成实质开发后再提交。

## 发布状态

`dist/index.html` 和 `functions/handler.js` 是 `npm run build` 从同一份源码生成的待发布资产。
`.任务进度追踪器.qoder.site` 保留原站点关联和旧发布快照，不手工伪造新的 releaseId、deploymentId 或发布成功状态。
仅提交或推送 GitHub 不会更新线上站点；必须在 Qoder Sites 再次发布。
生产服务地址、认证、Scope、公开可访问状态和线上验证结果需在真正发布后核对。

## 材料清单

| 材料 | 位置或所需动作 |
| --- | --- |
| 项目源码 | src/、public/、functions/、scripts/ |
| 运行部署说明 | README.md、DEPLOY.md |
| 验证样例 | VERIFY.md、test/ |
| 实际集成测试凭据 | 运行测试生成 test-results/integration.json，提交前脱敏 |
| 项目网站 | 在 Qoder 发布修复版，再无痕验证 |
| 演示截图或录屏 | public/demo.png 为本地真实服务截图；生产发布后再补线上截图 |
| Qoder 开发记录与 UID | 由参赛者提供真实记录 |
| 使用效果 | 可选；只提交可核对的真实数据 |
