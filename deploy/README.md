# 🚀 面板部署教程

[English](README.en.md) · [返回首页](../README.md)

> ⚠️ 项目仍在开发中。连接真实服务器或执行写操作前，请务必备份游戏服务器配置与数据。

支持 HTTP 和 HTTPS。**建议不要使用公网 HTTP 部署**：账号密码、登录会话和提交的 RCON 密钥可能被旁观者读取；提醒弹窗不能替代加密。没有域名时，可先通过 SSH 隧道或受控内网访问。

## 1. 准备与启动

需要 Docker Compose；从源码部署还需 Node.js、pnpm。Release ZIP 已含 `frontend/dist`，可以跳过前端构建；GitHub 自动生成的源码压缩包需要构建。

```bash
cd frontend
pnpm install --frozen-lockfile
pnpm run build
cd ..
cp deploy/panel.env.example deploy/panel.env
chmod 600 deploy/panel.env
mkdir -p data
sudo chown 10001:10001 data
```

在 `deploy/panel.env` 中填写 `PANEL_PUBLIC_ORIGIN` 与 `PANEL_SESSION_SECURE`（见下文），生成一次并长期保存 `PANEL_CONFIG_KEY`：

```bash
docker compose -f deploy/compose.yaml build panel
docker compose -f deploy/compose.yaml run --rm --no-deps panel python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

将生成的密钥填写到配置后启动，再交互创建管理员：

```bash
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml exec panel python -m app.cli create-admin
```

密钥、密码、`panel.env`、数据库及备份不要上传 Git。升级时保持 `PANEL_CONFIG_KEY` 和 `data/`；替换密钥会使已保存的 RCON 凭据无法解密。Compose 将应用仅映射至 `127.0.0.1:18000`，反向代理部署在同一宿主机。

## 2. HTTP 部署（可用，但不建议公网使用）

配置示例，替换为浏览器实际访问地址；协议、域名/IP、端口必须一致，不包含路径或末尾 `/`：

```dotenv
PANEL_PUBLIC_ORIGIN=http://panel.example.com
PANEL_SESSION_SECURE=false
```

使用 [HTTP Nginx 模板](wardogs-rcon-panel.http.nginx.conf)，替换 `server_name`。Ubuntu/Debian 示例（需已安装 Nginx）：

```bash
sudo cp deploy/wardogs-rcon-panel.http.nginx.conf /etc/nginx/sites-available/wardogs-panel
sudo ln -s /etc/nginx/sites-available/wardogs-panel /etc/nginx/sites-enabled/wardogs-panel
sudo nginx -t
sudo systemctl reload nginx
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

开放所选端口的云安全组和主机防火墙。使用其他端口时同时修改 Nginx `listen` 和 `PANEL_PUBLIC_ORIGIN`。已有站点应合并配置，避免重复监听或覆盖。

每次用 HTTP 打开页面，会先显示 **“您未部署在https版本 请留意数据安全”**；确认后才显示登录页面，后端启动也记录相同提醒。HTTP 会话不设置 `Secure`，仍保留 `HttpOnly`、`SameSite=Strict` 和请求来源校验。未设置 `PANEL_SESSION_SECURE` 时按 origin 自动选择；显式配置与协议冲突会拒绝启动。

## 3. HTTPS 部署（推荐）

### Caddy 自动证书

1. 将自己的域名 DNS 指向面板主机，允许公网访问 80/443，确保没有其他服务占用这些端口。
2. 按 [Caddy 官方安装说明](https://caddyserver.com/docs/install) 安装，创建 `/etc/caddy/Caddyfile`：

```caddyfile
panel.example.com {
    reverse_proxy 127.0.0.1:18000
}
```

3. 将面板环境改为：

```dotenv
PANEL_PUBLIC_ORIGIN=https://panel.example.com
PANEL_SESSION_SECURE=true
```

4. 校验并加载配置（适用于官方 systemd 服务安装）：

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

Caddy 为符合条件的域名自动申请/续期证书并将 HTTP 跳转至 HTTPS，要求 DNS、证书验证网络及端口可用。参见 [自动 HTTPS](https://caddyserver.com/docs/automatic-https)。只使用公网 IP 时，不要直接套用域名示例；优先配置域名与有效证书。

### 已有 Nginx 与证书

使用 [HTTPS Nginx 模板](wardogs-rcon-panel.nginx.conf)，替换域名及证书路径，先取得有效证书，再执行 `nginx -t` 和 reload。证书申请及续期方式依所选证书服务配置。HTTPS origin 与 `PANEL_SESSION_SECURE=true` 同上。

### 从 HTTP 迁移

先备份面板数据库与配置，再配置证书和 HTTPS 代理，修改上述两个环境变量并重建容器。保留数据和密钥，不重新初始化管理员。访问新的 HTTPS 地址后重新登录；浏览器 HTTPS 访问不显示 HTTP 提醒。

## 4. 面板协议与 RCON 协议是两条连接

- 浏览器 → 面板：由 `PANEL_PUBLIC_ORIGIN` 和反向代理决定。
- 面板 → 游戏 RCON：由服务器设置中的 RCON 地址决定；面板使用 HTTPS **不会**加密公网 HTTP RCON 的 Bearer。

推荐给游戏 RCON 配置 HTTPS 反向代理或 VPN/私网通道。HTTPS 代理应部署在游戏服务器同机或可信私网，避免代理后的流量仍经过公网 HTTP。

只有接受明文风险时，才在 `deploy/panel.env` 设置：

```dotenv
PANEL_ALLOW_PUBLIC_HTTP_RCON=true
```

然后执行：

```bash
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

回到“服务器设置”，勾选“允许公网 HTTP RCON”，填写 RCON 地址及 RCON 密码（Bearer 密钥），确认风险后保存。如果通过环境变量预配置 RCON，还需 `WARDOGS_ALLOW_PUBLIC_HTTP=true`。不要把 Bearer 填入前端环境变量或 Nginx 配置。门禁默认关闭；页面会显示开启方法。

## 5. 上线检查与更新

检查容器健康状态、页面可达性、登录及只读查询。服务器有玩家时不要测试踢出、封禁、警告、比赛控制或配置写入。结果不确定的写请求先人工核查，不自动重复。

导航栏支持 GitHub、Gitee 和自动选择，比较最新稳定版本，同版本优先 Gitee。Release 为空或缺少可校验安装包时，读取 `updates` 分支的 `latest.json`；该分支仅发布构建完成并带 SHA-256 的发行包，不直接安装主分支源码。更新源不可用时明确显示失败。

### 安装宿主机更新服务（Linux Docker 部署）

先备份数据库与环境文件。以下示例假定面板目录是 `/opt/wardogs-rcon-panel`，容器名为 `wardogs-rcon-panel`，数据文件为 `data/panel.sqlite3`，使用本项目 Compose 模板。自定义路径请同时修改 service 的 `ExecStart`，其他部署形式使用手动更新。

```bash
cd /opt/wardogs-rcon-panel
sudo install -m 700 deploy/update-agent.py /usr/local/sbin/wardogs-panel-updater
sudo install -m 644 deploy/wardogs-panel-updater.service /etc/systemd/system/
sudo install -m 644 deploy/wardogs-panel-updater.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now wardogs-panel-updater.timer
```

更新服务以 root 运行，具有 Docker 管理权限；只安装固定仓库发行包。面板容器不挂载 Docker socket。管理员在“检查更新”中输入当前密码，可安装一次或启用后台自动安装。**自动安装默认关闭**，开启后每小时检查；安装时面板短暂离线，游戏服务器不重启。

服务独立下载并核对 SHA-256、检查压缩包路径和版本、备份 SQLite 数据库、构建镜像，再检查新版健康状态。失败时尝试恢复旧镜像与数据库；中断任务不会自动重放。备份在 `.updates-host/`，状态在 `data/updates/`。更新失败需要人工查看 `systemctl status wardogs-panel-updater.service`；不要删除备份。发行包不会覆盖 `panel.env`、Compose、服务器连接或管理员账户。宿主机更新程序本身有改动时，重新执行上面的 `install` 命令。

### 自动发布 Gitee 与空 Release 回退

GitHub 仓库管理员在 Actions secrets 中配置 `GITEE_TOKEN`（Gitee 仓库代码与 Release 写入权限）。发布稳定 GitHub Release，并附带 `Wardogs_RCON_PANEL-v版本.zip` 与 `SHA256SUMS.txt` 后，`publish-gitee.yml` 同步主分支/标签、更新 Gitee 的 `updates` 分支，并发布同版本 Gitee Release 与附件。也可在 Actions 手动输入已有标签补发。不要将访问令牌写入文件或提交。

Gitee Release 暂不可用时，面板读取备用分支上的构建包与校验清单；自动模式还会检查 GitHub。两者均不可用则停止更新，保留当前版本。包与校验清单来自相同受信仓库，不是独立数字签名；仓库维护者账户应妥善保护。
