# 部署指南

## 本地开发

1. 安装依赖：
```bash
npm install
```

2. 配置环境变量：
```bash
cp .env.example .env
# 编辑 .env 文件，填入 PowerContext 配置
```

3. 启动 PowerContext Server（如果尚未运行）：
```bash
powercontext server run --env-file .env
```

4. 启动应用：
```bash
npm start
```

访问 http://localhost:3000

## Qoder Sites 部署

1. 确保项目根目录包含 `.站点名称.qoder.site` 文件

2. 使用 Qoder Sites 发布：
```bash
# 在 Qoder 中执行发布命令
```

3. 配置生产环境的 PowerContext 服务器地址

## 功能说明

### 核心功能
- 任务创建、编辑、删除
- 进度跟踪（0-100%）
- 优先级管理（高/中/低）
- 截止日期设置
- 任务状态自动更新

### PowerContext 集成
- **Memory**: 任务状态变更自动保存到 PowerContext Memory
- **Handoff**: 支持创建任务交接记录，便于跨会话工作
- **跨会话持久化**: 任务数据通过 PowerContext 实现会话间共享

### API 端点
- `GET /api/tasks` - 获取所有任务
- `POST /api/tasks` - 创建新任务
- `PUT /api/tasks/:id` - 更新任务
- `DELETE /api/tasks/:id` - 删除任务
- `POST /api/handoff` - 创建交接记录
- `GET /api/summary` - 获取统计摘要
