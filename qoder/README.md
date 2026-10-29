# PowerContext 任务进度追踪器

解决"反复确认任务进度"的问题，使用 PowerContext 实现跨会话的任务状态管理。

## 功能特性

- **任务创建与管理**: 创建任务并跟踪进度状态
- **跨会话持久化**: 使用 PowerContext Memory 保存任务状态，会话切换不丢失
- **进度更新**: 实时更新任务进度，支持百分比和状态标记
- **任务交接**: 使用 PowerContext Handoff 在会话间传递工作上下文
- **进度仪表盘**: 可视化展示所有任务的当前状态

## 技术栈

- **前端**: HTML5 + CSS3 + Vanilla JavaScript
- **后端**: Node.js + Express
- **上下文服务**: PowerContext HTTP API

## 快速开始

### 1. 安装依赖

```bash
npm install
```

### 2. 配置 PowerContext

确保 PowerContext Server 正在运行，并配置环境变量：

```bash
# .env 文件
POWERCONTEXT_URL=http://127.0.0.1:8000
POWERCONTEXT_SCOPE_ID=task-tracker-scope
POWERCONTEXT_AUTH_HEADER=
```

### 3. 启动应用

```bash
npm start
```

访问 http://localhost:3000

## 工作原理

### PowerContext 集成

本应用使用 PowerContext 的两个核心功能：

1. **Memory（记忆）**: 存储任务决策、约束和进度状态
   - 每个任务作为一个 Memory 条目保存
   - 支持跨会话检索和更新

2. **Handoff（交接）**: 在会话间传递工作上下文
   - 记录当前工作进展
   - 支持任务交接给其他会话或人员

### API 端点

- `POST /api/tasks` - 创建新任务
- `GET /api/tasks` - 获取所有任务
- `PUT /api/tasks/:id` - 更新任务进度
- `DELETE /api/tasks/:id` - 删除任务
- `POST /api/handoff` - 创建任务交接记录

## 项目结构

```
task-tracker/
├── src/
│   ├── server.js          # Express 服务器
│   └── powercontext.js    # PowerContext API 客户端
├── public/
│   ├── index.html         # 主页面
│   ├── style.css          # 样式文件
│   └── app.js             # 前端逻辑
├── api/
│   ── routes.js          # API 路由
── package.json
```

## 使用场景

1. **个人任务管理**: 跟踪个人工作任务的进度
2. **团队协作**: 在团队成员间交接任务上下文
3. **跨会话工作**: 在不同工作会话间保持任务状态连续性

## 许可证

Apache License 2.0
