# QQ 机器人：创建、配置与使用

普通群成员**不需要调用 API、不需要密钥，也不需要打开终端**。在已激活的 QQ 群里发命令即可；机器人在后台查询游戏服务器或面板，再返回图片或文字。

群成员看第一部分，搭建机器人的管理员看第二部分。本文地址和账号均为占位值，不是公共服务。

## 一、群成员怎么使用

先把机器人加入群，由机器人超级管理员发送 `/激活本群`。群主身份不能代替超级管理员激活群。当前实现采用 OneBot V11，不是 QQ 官方机器人版本。

### 查询服务器

| 在群里发送 | 用途 |
| --- | --- |
| `/服务器帮助` | 查看可用命令和参数 |
| `/战况` | 当前地图、人数和阵营比分 |
| `/玩家列表 [名称关键词]` | 在线玩家；无关键词查询全部，也可用 `/玩家` |
| `/轮换` | 地图轮换列表 |
| `/地图 [关键词]` | 可用地图及筛选 |
| `/模式 [地图标识]` | 可用模式 |
| `/天气` | 可用天气/光照及中文说明 |

`[...]` 表示可选参数，不要输入方括号。地图、模式、天气的实际标识以查询结果为准。

### 查看自己的战绩和预留位

在群里发送 `/绑定`，后接空格和**自己的 17 位 SteamID64**，例如命令形式 `/绑定 <你的17位SteamID64>`；尖括号需替换为实际数字，不要输入 Steam 昵称、好友代码或个人主页 URL。

之后直接发送：

```text
/我的
/我的预留位
```

`/我的` 查询本服被面板记录的战绩、排名和参考分，不是游戏官方完整生涯。采集前的数据无法恢复，缺失等级或统计显示暂无数据，不代表零。参考分按击杀排名计算，不是作弊判定或真实能力认证。

绑定只检查格式并登记关系，**不验证 Steam 账号所有权**；在已激活群可再次发送 `/绑定` 更新登记。查询预留位不等于获得预留位，是否有效仍取决于服务器与面板记录。

### 管理命令及确认

只有超级管理员、当前群群主/管理员或本群已授权用户可执行管理命令。

| 命令形式 | 用途 |
| --- | --- |
| `/封禁列表` | 查询封禁名单 |
| `/预留位` | 查询服务器预留位 |
| `/管理日志 [条数]` | 查询日志，条数为 1–50 |
| `/服务器配置` | 查询安全字段摘要，不返回密码等原始配置 |
| `/踢出 <玩家名或SteamID64> [原因]` | 踢出目标 |
| `/封禁 <玩家名或SteamID64> [原因]` | 通过 RCON 封禁目标 |
| `/面板封禁 <玩家名或SteamID64> [原因]` | 通过面板封禁在线目标 |
| `/解封 <SteamID64>` | 解除封禁 |
| `/换图 <地图> [模式] [天气] [区域变体]` | 切换地图 |
| `/重启比赛` | 重开当前比赛，不是重启服务器进程 |

`<...>` 表示必填，`[...]` 表示可选，均不要原样输入。写命令**不会立即执行**：机器人先显示目标、参数和确认短码。核对后，原操作者在原群 60 秒内发送 `/确认 <短码>`；不执行则发 `/取消 <短码>`。确认时仍会检查权限和能力；名称匹配多人时需明确目标。

写操作不自动重试。“结果未知”不等于失败，应先查询玩家或封禁列表核实。初次验收仅使用查询命令，不用真实封禁、踢出或换图测试。

超级管理员还可使用 `/激活本群`、`/停用本群`、`/授权 @用户`、`/取消授权 @用户`、`/权限列表`。`@用户` 必须使用 QQ 实际的 @ 提及，当前实现不是输入一串 QQ 数字。授权仅对本群生效，停用会取消该群待确认操作；业务查询不支持私聊或临时会话。

## 二、管理员怎么创建和接入机器人

### 1. 准备三个独立组件

| 组件 | 用途 | 需要的配置 |
| --- | --- | --- |
| NapCat / OneBot V11 | 收发 QQ 消息 | 机器人 QQ、WebSocket 地址、OneBot Token |
| 游戏服务器 RCON | 实时查询和服务器管理 | RCON 地址和密码 |
| Wardogs RCON Panel | 个人战绩、排名、个人预留位及面板封禁 | 面板地址、查询密钥；管理密钥按需设置 |

三种凭据互不通用，面板网页登录密码也不是机器人密钥。本仓库提供面板和接入文档，**当前不包含 `wardogs_qq` 机器人主程序**；需另外取得完整源码目录（包括 `bot.py`、`pyproject.toml`、`wardogs_qq/`）。下载面板 Release 不等于已经取得 QQ 机器人。

### 2. 在面板侧生成密钥：解决截图中的报错

下面的命令**当前不存在，请不要执行**：

```text
python -m app.cli create-bot-token --read-only
```

当前 `app.cli` 只有 `create-admin`，上述命令会报 `invalid choice`。`create-admin` 用于创建网页登录账号，不能创建机器人密钥。

正确做法：在面板项目根目录执行随机密钥生成命令。已有 Docker 面板无需额外安装 Python，Windows PowerShell 和 Linux 终端均可执行：

```sh
docker compose -f deploy/compose.yaml exec -T panel python -c "import secrets; print(secrets.token_urlsafe(48))"
```

生成一次作为查询密钥；需要面板封禁时再执行一次作为管理密钥。输出只保存在管理员自己的终端，不要截图、分享或提交 Git。两者必须不同，也不能与游戏事件推送 Token 共用；面板要求至少 32 个可打印 ASCII 字符。

用编辑器打开实际 Compose 服务 `env_file` 指向的文件，**只修改以下配置，不覆盖原文件**：

```env
PANEL_BOT_READ_TOKEN=粘贴刚生成的查询密钥
PANEL_BOT_ADMIN_TOKEN=粘贴另一条刚生成的管理密钥
```

中文说明必须替换为随机 ASCII 密钥。不需要面板封禁时，`PANEL_BOT_ADMIN_TOKEN=` 留空。模板默认使用 `deploy/panel.env`，已有部署可能另有路径；以自己的 `deploy/compose.yaml` 中 `env_file` 为准，不要盲目创建无效配置。

保存后，在面板项目根目录执行：

```sh
docker compose -f deploy/compose.yaml up -d --no-deps --force-recreate panel
```

只重建面板容器，不重启游戏或 NapCat。**单纯 `docker compose restart` 不会重新加载修改后的环境变量。** 旧版没有 Bot 接口时先按[面板部署教程](../deploy/README.md)升级。不要修改原有 `PANEL_CONFIG_KEY`、RCON 凭据、数据挂载或端口，否则可能影响已有数据与配置。

### 3. 配置 NapCat

用独立 QQ 账号登录 NapCat，将机器人加入目标群。在 OneBot V11 网络配置中启用**正向 WebSocket 服务端**，设置监听地址、端口和访问 Token，让机器人主动连接它。

- WebSocket 示例端口为 `3001`，按实际配置填写。WebUI 管理页面常用 `6099`，不能用作 WebSocket 地址。
- Docker 中还需映射端口；监听 `0.0.0.0` 不代表防火墙或安全组已经放行。
- 不同容器中的 `127.0.0.1` 指向各自容器，使用服务名或实际可达地址；同机非容器部署可使用回环地址。
- 不要把面板网址填进 NapCat，也不要公开无鉴权 OneBot 服务。优先内网或加密连接。

### 4. 安装并配置机器人程序

进入另外取得的完整 `wardogs_qq` 源码目录，建议使用 Python 3.11。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
# 仅在还没有 .env 时执行，不覆盖已有配置
Copy-Item .env.example .env
```

Linux：

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
cp -n .env.example .env
```

编辑机器人项目根目录的 `.env`：

```env
ENVIRONMENT=prod
LOG_LEVEL=INFO
DRIVER=~fastapi+~websockets+~httpx
HOST=127.0.0.1
PORT=8080
LOCALSTORE_USE_CWD=true
COMMAND_START=["/"]

ONEBOT_WS_URLS=["ws://napcat.example.invalid:3001"]
ONEBOT_ACCESS_TOKEN="替换为NapCat中设置的Token"
SUPERUSERS=["100000001"]
WARDOGS_SUPERUSER_QQ=100000001

WARDOGS_RCON_BASE_URL=https://rcon.example.invalid:9191
WARDOGS_RCON_PASSWORD="替换为游戏服务器RCON密码"
WARDOGS_ALLOW_INSECURE_HTTP=false

WARDOGS_PANEL_BASE_URL=https://panel.example.invalid/api/bot
WARDOGS_PANEL_READ_TOKEN="与PANEL_BOT_READ_TOKEN完全相同"
WARDOGS_PANEL_ADMIN_TOKEN=
WARDOGS_PANEL_ALLOW_INSECURE_HTTP=false
WARDOGS_PAGE_SIZE=12
WARDOGS_READ_RETRY_COUNT=2
```

示例域名不可用，必须替换。`100000001` 也是示例 QQ；将 `SUPERUSERS` 和 `WARDOGS_SUPERUSER_QQ` 同时改为自己的管理员 QQ，群激活与授权实际以后者为准。需要面板封禁时，将 `WARDOGS_PANEL_ADMIN_TOKEN` 填为与 `PANEL_BOT_ADMIN_TOKEN` 完全相同的管理密钥。

默认使用 localstore 数据目录，需确保可写并备份。如需指定数据库可设 `WARDOGS_DATABASE_PATH=./data/wardogs.sqlite3`；不要把 Linux 的 `/data/...` 路径直接照搬到 Windows。

地址末尾 `/api/bot` 供机器人后台使用，**不要求群成员手动访问**。不要填面板登录页面、NapCat WebUI 或 OneBot 地址。

上游只能用 HTTP 时，管理员明确接受风险后分别启用对应开关，例如：

```env
WARDOGS_PANEL_BASE_URL=http://panel.example.invalid:23334/api/bot
WARDOGS_PANEL_ALLOW_INSECURE_HTTP=true
```

RCON 使用 HTTP 时另设 `WARDOGS_ALLOW_INSECURE_HTTP=true`，两个开关互不替代。HTTP/WS 明文通信可能暴露密钥，优先 HTTPS/WSS、内网或可信隧道。自签 HTTPS 证书通过 `WARDOGS_PANEL_CA_FILE` / `WARDOGS_RCON_CA_FILE` 指定可信 CA，不关闭证书校验。

### 5. 启动和群内验收

从机器人项目根目录启动并保持进程运行：

```powershell
# Windows
.\.venv\Scripts\python.exe bot.py
```

```sh
# Linux
.venv/bin/python bot.py
```

生产环境不要启用 DEBUG 或 `--reload`，分享日志前先脱敏。修改配置后重启机器人；若存在 `.env.prod` 等文件，还需检查是否覆盖同名设置。

超级管理员在目标群依次发送 `/激活本群`，再用 `/服务器帮助`、`/战况`、`/玩家列表` 验证 RCON 查询。随后绑定自己的 SteamID64，发送 `/我的`、`/我的预留位` 验证面板查询。没有观测统计与网络错误是不同问题；验收不执行真实处罚。

图片失败应回退文字。环境缺少中文字体时，可用 `WARDOGS_FONT_PATH` 指定可读取的中文字体文件。在 1Panel Python 运行环境中，运行目录设为完整机器人目录，安装依赖后用 `python bot.py` 启动并持久化配置及数据库。主动连接 NapCat 通常不需要将 `8080` 暴露公网；当前项目没有附带现成的机器人 Docker 镜像。

## 三、常见问题

| 提示或现象 | 检查与处理 |
| --- | --- |
| `create-bot-token` / `invalid choice` | 当前没有该 CLI 子命令，按本文生成随机密钥并配置环境文件 |
| 机器人不回复 | 程序是否运行、QQ 是否在线、WebSocket 是否连接、是否在已激活群 |
| `/我的` 提示面板超时或不可达 | 从机器人所在机器检查面板地址、端口和网络；OneBot 连通不等于面板连通 |
| HTTP 不允许 | 使用 HTTPS，或由管理员接受风险后启用对应开关，不混用 RCON 与面板开关 |
| 配置后仍拒绝请求 | 两侧密钥是否相同、是否误用登录密码、面板是否重建加载环境、机器人是否重启 |
| 管理命令被拒绝 | 群是否激活、当前群权限、管理密钥和上游能力是否具备 |
| 战绩或等级暂无数据 | 面板只返回已记录的数据，不补造；绑定本身不生成战绩 |
| 确认码无效 | 原操作者、原群、60 秒期限及一次性限制是否满足 |

仅管理员需要排查网络，例如在机器人所在 Windows 电脑执行下面的只读命令，并替换实际主机名和端口：

```powershell
Test-NetConnection panel.example.invalid -Port 23334
```

TCP 不通时增加重试次数不能解决端口或路由问题。普通成员把机器人返回的错误文字发给管理员即可，**不需要学习或自行调用 API**。

## 四、维护者接口参考（群成员无需操作）

面板接口需要服务端 Bearer 密钥，查询与管理身份分离。以[后端实现](../backend/app/api/bot.py)和带鉴权的 `/api/bot/openapi.json` 为准。

| 后台用途 | 接口 |
| --- | --- |
| 能力检查 | `GET /api/bot/capabilities` |
| 个人观测战绩 | `GET /api/bot/players/{steamId}/stats` |
| 个人击杀排名 | `GET /api/bot/players/{steamId}/ranking` |
| 个人预留位 | `GET /api/bot/players/{steamId}/reserved-slot` |
| 查找在线封禁目标（管理密钥） | `GET /api/bot/players` |
| 在线封禁（管理密钥） | `POST /api/bot/bans` |

不要把密钥放到 QQ 消息、前端代码、截图、公共日志或仓库里，不用真实处罚验证接口。
