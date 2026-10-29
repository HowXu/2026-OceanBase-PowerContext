# 部署独立 PowerContext 服务

当前项目没有已配置的公网 PowerContext。这里提供待执行的服务器方案，不代表已经购买服务器、开放端口或完成生产验证。

## 所需条件

- 一台你有权限使用、可持续运行的 Linux 服务器，安装 Docker Engine 和 Docker Compose。
- 一个指向该服务器的域名，例如 `powercontext.your-domain.com`。
- 服务器能访问 GitHub、Python 包源和证书服务，允许入站 TCP 80/443（UDP 443 可选）。
- 持久磁盘空间，用于保存任务与交接记忆。

PowerContext 使用此前真实测试过的固定版本；数据写入持久 Docker volume。
Caddy 提供 HTTPS。PowerContext 的 8000 端口只在容器网络中可见，所有业务 API 要求 Bearer 认证。
不在镜像或命令参数中写入令牌，也不把认证令牌发送到网页。

## 在服务器上准备

下载修复分支，进入 `qoder/`，将域名替换成自己的真实域名：

```bash
python3 deploy/init-env.py --hostname powercontext.your-domain.com
docker compose --env-file deploy/.env.production -f deploy/compose.yaml config --quiet
```

初始化只在本机生成 `deploy/.env.production`，不会联网、启动服务或打印令牌，也不会覆盖已有文件。
Linux 下创建权限为 0600。该文件被 Git 和 Docker build context 排除。

**完成部署目标和公开访问确认后**，运行：

```bash
docker compose --env-file deploy/.env.production -f deploy/compose.yaml up -d --build
docker compose --env-file deploy/.env.production -f deploy/compose.yaml ps
curl --fail https://powercontext.your-domain.com/health/ready
```

首次构建需下载固定版本源码与 Python 依赖；证书签发要求真实 DNS 已生效。
不要使用 `docker compose config` 输出完整配置到公开日志，因为其中会展开令牌；使用 `config --quiet`。

## 与应用连接

1. 从私有配置文件读取 `POWERCONTEXT_SERVER_AUTH_TOKEN`，仅配置到应用 `.env` 或 Qoder Secrets 的 `POWERCONTEXT_TOKEN`。
2. 应用 `POWERCONTEXT_URL` 设为 `https://你的域名`。
3. 应用执行 `npm run setup:scope`，取得真实 Scope ID；已有专用 Scope 时直接填入，不重复创建。
4. 将相同 URL、Token 和 Scope ID 配置到 Qoder Function 的 Secrets。
5. 按上级 VERIFY.md 完成测试，通过 Qoder Sites 发布修复版，再用无痕窗口验证。

验收还应确认未携带认证的 `/v1/atomic-memory/list` 请求被拒绝、服务重启后记录仍在。
`/health/ready` 公开可访问属于 PowerContext 的正常健康检查行为。

## 备份与停止

```bash
docker compose --env-file deploy/.env.production -f deploy/compose.yaml stop
```

停止服务会使线上功能暂时不可用，但不删除数据。重启使用 `up -d`。
不要使用 `down --volumes`，该参数会删除记忆和证书 volume。
应由服务器管理者制定并验证持久 volume 的备份和恢复流程。

## 暂无服务器时

可以继续本地真实测试。若需短期在线联调，可在单独确认后使用带认证的临时隧道。
临时隧道依赖电脑与进程保持运行，地址可能改变，不应视为稳定的最终评审部署。
不要把未启用认证的本机 PowerContext 直接暴露到公网。

本机 WSL 内已有 Docker Compose 2.40.3；`config --quiet` 配置验证、凭据生成、拒绝覆盖和域名输入校验均通过。
当前用户访问 Docker daemon 返回 permission denied，因此尚未构建或运行容器，也未验证公网 HTTPS。
本次没有修改 Docker socket 权限或用户组。

参考：[PowerContext 部署说明](https://github.com/oceanbase/powercontext/blob/bf2ac1fcd9223af7bbab97f8e8e39a269b07fee3/docs/zh/docs/operate/deploy-server.md)、[Caddy 自动 HTTPS](https://caddyserver.com/docs/automatic-https)。
