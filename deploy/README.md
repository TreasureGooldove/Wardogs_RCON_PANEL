# Docker Compose 部署模板

本目录提供通用部署示例，不包含任何现网服务器地址、端口、凭据、证书或管理员密码。请在自己的主机上调整域名、端口、持久化目录和反向代理设置。

> ⚠️ 项目仍在开发中。连接真实服务器或执行写操作前，请务必备份游戏服务器配置与数据。

从仓库根目录先构建前端：

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
```

复制配置模板并填写本机值：

```bash
cp deploy/panel.env.example deploy/panel.env
chmod 600 deploy/panel.env
mkdir -p data
sudo chown 10001:10001 data
```

为 `PANEL_CONFIG_KEY` 生成并长期保留 Fernet 密钥。`PANEL_PUBLIC_ORIGIN` 必须与浏览器实际访问的 HTTPS origin 完全一致。真实 RCON Bearer、Steam Web API Key 和管理员密码只在受控环境中设置，绝不能提交到 Git。若 RCON 只有公网 HTTP，启用前应明确了解高权限 Bearer 会明文传输；模板默认关闭这一能力。

启动面板：

```bash
docker compose -f deploy/compose.yaml up -d --build
docker compose -f deploy/compose.yaml exec panel python -m app.cli create-admin
```

`compose.yaml` 示例将应用仅映射至宿主机 `127.0.0.1:18000`，数据库持久化到项目根目录的 `data/`。`wardogs-rcon-panel.nginx.conf` 是 HTTPS `443` 的示例反向代理配置；将示例域名和证书路径换成自己的值，运行 `nginx -t` 后再加载配置。

上线后检查容器健康状态、HTTPS 页面和经登录的只读接口。服务器有玩家在线时，不进行踢出、封禁、警告、比赛控制或配置写入测试。RCON 写请求的结果不确定时，先人工核查，不自动重复提交。
