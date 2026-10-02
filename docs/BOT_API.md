# 🤖 机器人 API 文档

简体中文 | [English（AI 翻译）](BOT_API.en.md) | [OpenAPI 3.1](bot-openapi.json) | [返回主页](../README.md)

契约版本：`apiVersion=1`，`build=bot-api-1`。本文依据仓库中的 [机器人路由](../backend/app/api/bot.py)、[错误处理](../backend/app/errors.py) 和 [网关诊断](../backend/app/api/diagnostics.py) 编写。所有示例均为虚构数据，不包含可用密钥或真实服务器信息。

本文说明面板提供给外部机器人的 API，不是游戏服务器原始 RCON API。完整面板接口的在线定义位于面板源站 `/openapi.json`；其中的业务权限仍由面板登录和各路由检查控制。机器人使用下面的专用 Token，不使用管理员 Cookie。

## 1. 地址与权限

支持两种入口，路径相同：

| 入口示例 | 用途 |
| --- | --- |
| `https://panel.example.com:23333/api/bot/…` | 使用面板 HTTPS 入口 |
| `http://panel.example.com:23334/api/bot/…` | 可选的独立 HTTP 网关，仅代理机器人接口 |

23333、23334 是示例部署端口，可自行配置。HTTP 不加密，Token 和数据会以明文传输；建议使用 HTTPS 或可信内网，确需公网 HTTP 时限制来源地址。浏览器面板仍使用自身源站，不应改为请求 HTTP 网关。

请求头：`Authorization: Bearer <机器人Token>`。不要放在 URL、前端代码、群消息或日志中。

| 环境变量 | 权限 | 可调用接口 |
| --- | --- | --- |
| `PANEL_BOT_READ_TOKEN` | `personal:read` | 能力、个人战绩、排名、个人预留位、受保护 OpenAPI |
| `PANEL_BOT_ADMIN_TOKEN` | 上述权限及 `players:read`、`bans:write` | 再增加在线玩家查找、封禁 |

Token 是机器人身份；`operator.qqId/groupId` 只是调用者提交的审计信息，不能证明 QQ 身份。机器人必须自行检查群、操作者权限，并将管理 Token 留在机器人服务端。

面板不保存 QQ 绑定。机器人可本地记录 QQ → SteamID64；17 位纯数字检查只验证格式，不能证明 Steam 账号所有权，也不能授予封禁权限。只读 Token 能查询任意格式有效的 SteamID，机器人应自行限制普通用户只能查询其登记的信息。

## 2. 部署配置

在受保护终端分别执行两次，生成不同 Token：

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将结果分别填入部署主机的受保护环境文件（默认 Compose 使用 `deploy/panel.env`）：

```dotenv
PANEL_BOT_READ_TOKEN=
PANEL_BOT_ADMIN_TOKEN=
# 可选；留空时，状态检查使用 PANEL_PUBLIC_ORIGIN。
PANEL_BOT_PUBLIC_ORIGIN=http://panel.example.com:23334
```

空值会禁用对应身份。Token 至少 32 个 ASCII 字符，不包含空白或控制字符；读、管理及击杀推送 Token 必须互不相同。项目没有 `create-bot-token` CLI 命令。不要将生成结果写入仓库；Linux 环境文件应限制为所有者可读写（如 `chmod 600 deploy/panel.env`）。

`PANEL_BOT_PUBLIC_ORIGIN` 只指定状态检查目标，不会创建监听端口；它的主机名必须与 `PANEL_PUBLIC_ORIGIN` 相同，协议和端口可以不同。地址不能包含路径、查询、账号密码或末尾斜杠。保持现有 `PANEL_CONFIG_KEY` 等密钥；HTTPS 面板保持 `PANEL_SESSION_SECURE=true`。

从项目根目录重新创建面板容器以加载环境变量：

```sh
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

仅 `restart` 不会加载修改后的 Compose 环境文件。此命令只作用于面板服务，首次安装和初始化管理员参见 [主页部署说明](../README.md)。

### 独立 HTTP 网关

使用 [Nginx 示例](../deploy/wardogs-bot-api.http.nginx.conf)，修改 `server_name` 与 `proxy_pass` 为自己的域名和实际后端监听地址。仓库 Compose 默认后端是 `127.0.0.1:18000`，现有部署应以实际映射为准。

将配置安装到系统 Nginx 的有效配置目录，执行 `nginx -t`，通过后再重载 Nginx；按需放行 23334，并限制机器人出口 IP。模板只代理 `/api/bot/`，去除 Cookie、不向客户端转发 Set-Cookie，其他路径返回 404；仅允许 GET/POST（Nginx 将 HEAD 按 GET 处理），请求体最大 16 KiB。面板登录、配置和诊断接口不在此入口开放。

重载后先等待匿名请求 `/api/bot/openapi.json` 返回 401，再使用 Token 做只读检查。不要为了处理 HTTPS 证书问题使用 `curl -k`；HTTPS 应配置有效证书或客户端信任的 CA。

## 3. 接口总览与公共约定

| 方法与路径 | 最低身份 | 作用 |
| --- | --- | --- |
| `GET /api/bot/capabilities` | 只读 | API 版本、服务器标识、可用功能、目标版本 |
| `GET /api/bot/players/{steamId}/stats` | 只读 | 本服累计观测战绩与当前比赛快照 |
| `GET /api/bot/players/{steamId}/ranking` | 只读 | 累计观测击杀排名 |
| `GET /api/bot/players/{steamId}/reserved-slot` | 只读 | 该玩家的运行预留位、配置报告与到期时间 |
| `GET /api/bot/players` | 管理 | 分页查找在线封禁目标 |
| `POST /api/bot/bans` | 管理 | 封禁在线玩家，持久幂等与审计 |
| `GET /api/bot/openapi.json` | 只读 | 获取在线机器人 OpenAPI，不调用游戏写操作 |

- GET 路径中的 `steamId` 必须为 17 位纯数字字符串；不要转成 JavaScript `Number`，避免精度丢失。
- 时间使用 ISO 8601，可能为 UTC `Z`；客户端自行转换时区。未知值为 `null`，不能当成 `0`。
- `Cache-Control: no-store`；`X-Request-ID` 用于排查请求。通用错误的 `requestId` 是追踪编号，与封禁提交的幂等 `requestId` 含义不同。
- 每个进程按 Token 角色共享限流：读请求 120 次/分钟，POST 10 次/分钟；同角色的多个机器人会共享额度。上游另有限流，没有保证返回 `Retry-After`。
- `targetRevision` 是不透明的面板目标版本，不是 INI 文件版本。重启面板或更换连接后应重新获取，不要缓存用于未来写操作。

## 4. 能力查询

`GET /api/bot/capabilities` 的虚构响应：

```json
{
  "apiVersion": "1", "panelVersion": "example-version", "build": "bot-api-1",
  "serverId": "example-server-id", "targetRevision": "example-target-revision",
  "permissions": ["personal:read", "players:read", "bans:write"],
  "features": {"stats": true, "ranking": true, "reservedSlot": true, "ban": true, "banOnlineOnly": true},
  "rankingPeriods": ["all"], "statsSource": "panel_observations",
  "officialCareer": false, "updatedAt": "2026-01-01T00:00:00Z"
}
```

`serverId` 为目标地址摘要，不返回地址或密码。只读身份的 `features.ban=false`。能力查询可能需要读取上游能力；启用功能不保证上游此刻可达，也不保证具备完整历史数据。

## 5. 个人战绩

`GET /api/bot/players/76561190000000001/stats`：

```json
{
  "steamId": "76561190000000001", "name": "Example Player", "online": true,
  "scope": "recorded_lifetime", "source": "panel_observations", "officialCareer": false,
  "updatedAt": "2026-01-01T00:00:00Z", "onlineObservedAt": "2026-01-01T00:00:00Z",
  "stats": {"kills": 120, "deaths": 80, "cash": 8000, "peakCash": 18000, "matches": 15,
            "wins": null, "playtimeSeconds": null, "battleLevel": null},
  "currentMatch": {"kills": 4, "deaths": 2, "cash": 8000, "pingMs": 60}
}
```

| 字段 | 说明 |
| --- | --- |
| `stats.kills/deaths/matches` | 面板已记录的本服累计击杀、死亡与观测对局数；不是官方生涯 |
| `stats.cash/peakCash` | 最近余额 / 观测最高余额；不是累计收入 |
| `wins/playtimeSeconds/battleLevel` | 当前无可靠来源，返回 `null` |
| `currentMatch` | 当前玩家快照，可能为 `null`；其中各数值也允许为 `null` |
| `online` | `true` 在线、`false` 未出现在新鲜名单中、`null` 名单已陈旧 |
| `updatedAt` | 该玩家最近历史观测时间，无历史则 `null` |
| `onlineObservedAt` | 在线名单快照采集时间，不等于请求时间 |

`online=null` 时，`currentMatch` 仍可能来自旧缓存，不能声称实时在线。未记录玩家的累计字段为 `null`，不是 0；没有记录不会返回“玩家不存在”错误。统计覆盖面板开始记录后的数据，短暂上下线和采样间事件可能遗漏。机器人自行生成评分时应注明指标和观测范围，不应伪造战斗等级、胜率或在线时长。

## 6. 排名

`GET /api/bot/players/76561190000000001/ranking?period=all`：

```json
{
  "steamId": "76561190000000001", "period": "all", "rank": 2, "totalPlayers": 3,
  "battleLevel": null, "rankBy": "kills", "kills": 120, "ties": "dense_rank",
  "includesOffline": true, "scope": "recorded_lifetime", "updatedAt": "2026-01-01T00:00:00Z"
}
```

只支持 `period=all`（默认）；其他周期返回 400。按本服累计观测击杀降序，包含离线玩家，排除击杀数未知者。采用密集排名：击杀 `150、150、120` 对应排名 `1、1、2`。无记录者 `rank/kills=null`，`totalPlayers` 仍为参与排名人数；`updatedAt` 是本服最近对局观测时间。

## 7. 个人预留位

`GET /api/bot/players/76561190000000001/reserved-slot`：

```json
{
  "steamId": "76561190000000001", "active": false, "configured": true, "pendingRestart": true,
  "expiresAt": "2026-01-02T00:00:00Z", "reason": "Example reward", "metadataStatus": "active",
  "updatedAt": "2026-01-01T00:00:00Z", "configuredSource": "rcon_config", "configurationVerified": false
}
```

- `active`：官方运行预留位列表当前是否包含该玩家。
- `configured`：官方 `/v1/config` 报告是否包含该玩家；配置路由不支持时为 `null`，其他读取错误正常返回错误。
- `pendingRestart`：已知配置与运行状态是否不同（`active != configured`），未知为 `null`。这是差异推断，不保证重启一定解决。
- `configuredSource`：读取到配置时为 `rcon_config`，否则 `null`。`configurationVerified=false` 表明官方配置报告未被证明与磁盘文件一致。
- `expiresAt/reason/metadataStatus`：面板保存的到期时间、原因和管理状态，无记录则为 `null`；没有到期时间不一定表示玩家拥有永久位。

仅返回指定玩家，不返回全量名单或配置密码。到期元数据不等于运行状态，不能只凭 `expiresAt` 判断已删除或已生效；应结合 `active`，必要时提示“配置报告已添加，当前运行尚未生效”。

## 8. 管理员查找目标

`GET /api/bot/players?search=Example&limit=20&offset=0`，仅管理 Token：

| 参数 | 默认值 | 限制 |
| --- | --- | --- |
| `search` | 空字符串 | 最长 64 字符；姓名不区分大小写子串，或 SteamID 子串 |
| `limit` | 20 | 1–50 |
| `offset` | 0 | 0–10000 |

```json
{
  "items": [{"steamId": "76561190000000001", "name": "Example Player"}],
  "total": 1, "limit": 20, "offset": 0, "targetRevision": "example-target-revision",
  "stale": false, "updatedAt": "2026-01-01T00:00:00Z", "scope": "online"
}
```

`total` 是筛选后的总数。空搜索可分页取得在线名单，但仍必须管理鉴权；这不是公开接口。`stale=true` 表示旧缓存，不应据此确认玩家现在在线。机器人让管理员选定准确 SteamID 后再封禁，不要按同名昵称直接处罚。

## 9. 封禁与幂等

`POST /api/bot/bans`，管理 Token，`Content-Type: application/json`。仅支持在线玩家；不存在机器人解封、离线配置封禁或封禁列表接口。

```json
{
  "steamId": "76561190000000001", "reason": "Example moderation reason",
  "targetRevision": "example-target-revision", "requestId": "bot-example-operation-0001",
  "operator": {"qqId": "10000", "groupId": "20000"}
}
```

| 字段 | 校验 |
| --- | --- |
| `steamId` | 17 位纯数字字符串，首位不能为 0 |
| `reason` | 1–200 字符，不得全空白或含控制字符；前后空白会去除 |
| `targetRevision` | 1–128 字符；使用刚获取的目标版本 |
| `requestId` | 8–128 字符，只允许字母、数字、`_ . : -`；一次操作唯一 |
| `operator.qqId/groupId` | 1–20 位数字字符串；操作人和操作群 |

请求及 `operator` 对象禁止多余字段。面板记录操作人、群、目标、原因和结果，独立于面板管理员身份。

```json
{
  "requestId": "bot-example-operation-0001", "steamId": "76561190000000001",
  "outcome": "accepted", "code": "accepted", "replayed": false, "updatedAt": "2026-01-01T00:00:00Z"
}
```

| 结果 | 首次 HTTP 状态 | 重放 HTTP 状态 | 客户端处理 |
| --- | --- | --- | --- |
| `accepted` | 200 | 200 | RCON 已接受；不等于已校验磁盘持久化 |
| `rejected` | 409 | 409 | 查看原因，修正前停止；拒绝可能发生于目标版本、权限、在线检查或上游 |
| `uncertain` | 502 | 409 | 停止自动重发，管理员核查实际封禁列表与审计 |

同一 `requestId` 与相同规范化请求重放返回已有结果，不重复执行，`replayed=true`；换原因、目标版本、操作人等仍使用原编号会返回通用错误 `409 bot_request_conflict`。记录保存在面板数据库，跨进程重启及 Token 轮换保留；备份数据库，不要清空幂等记录。并发中或中断未完成的原操作重放可能返回 `uncertain / operation_in_progress_or_interrupted`，`updatedAt` 可缺失或为 `null`。

建议流程：校验 QQ 管理员 → 查询能力和在线目标 → 人工确认玩家与原因 → 生成唯一编号并保存完整请求 → 提交一次 → 按结果展示。`stale_server_target` 应重新获取目标；`player_not_online` 不允许继续封禁。`action_uncertain`、`internal_error` 或 `audit_completion_failed` 等需要查看 `outcome` 判断，不能只凭 HTTP 状态。

连接中断或超时后，不要换新编号重试，也不要声称成功；可重放完全相同的原请求获得已有回执，但它不会强制重新执行或补全未决操作。玩家离线不证明封禁成功。当前没有独立的操作状态查询接口；实际封禁列表需由管理员在面板或原 RCON 工具核查。

鉴权、参数校验、幂等冲突等可能直接返回通用错误，不是封禁回执；客户端必须支持两种响应结构。

## 10. 错误码

通用错误示例：

```json
{"code": "bot_invalid_request", "message": "机器人请求参数无效", "requestId": "example-trace-id"}
```

| HTTP | 通用错误码 | 说明 |
| --- | --- | --- |
| 400 | `bot_invalid_request` | 路径、查询或请求体校验失败；实际不是 FastAPI 默认的 422 |
| 401 | `not_authenticated` | Token 缺失、错误或对应身份已禁用；不是要求机器人登录 Cookie |
| 403 | `permission_denied` | 只读 Token 调用管理接口 |
| 409 | `bot_request_conflict` | 幂等编号已用于不同请求 |
| 429 | `rate_limited`、`rcon_rate_limited` | 面板或游戏 RCON 限流 |
| 501 | `route_unsupported`、`action_unsupported` | 官方未公告相应能力 |
| 502 | `rcon_auth_failed`、`invalid_upstream` | 上游认证失败或数据无法解析 |
| 503 | `rcon_unconfigured`、`rcon_unavailable` | 未配置目标或不可达 |
| 504 | `rcon_timeout` | 上游读取超时 |

封禁通过领取幂等编号后的错误放入回执，HTTP 按上一节映射；回执 `code` 还可能为 `write_disabled`、`stale_server_target`、`player_not_online`、`action_rejected`、`action_uncertain`、`internal_error`、`audit_completion_failed`。消息可能为中文，程序应解析 `code/outcome`，不要匹配文本。未处理内部异常可能为 500，不保证符合通用错误结构。

Nginx 404、方法拒绝 403、请求体过大 413 等可能返回 HTML；DNS、TCP、TLS 失败没有 API JSON。客户端先检查状态和内容类型，再解析。

## 11. 面板查看 API 状态

主账号登录 → **服务器诊断 → 机器人 API 状态**，点击重新查询。

对应 `GET /api/server/bot-api-status`，使用面板主账号 Cookie，**不能用机器人 Token**，且不在 23334 网关开放。返回 `apiVersion`、`panelVersion`、`gatewayOrigin`、`encrypted`、`observedAt`、`probeSource=panel_server`、`probePath=/api/bot/openapi.json` 和 `credentials` 数组。每项包含 `role`、`configured`、`permissions`、`state`、`httpStatus`、`latencyMs`，不返回密钥。

| `state` | 含义 |
| --- | --- |
| `available` | 携带该角色 Token 成功读取并校验机器人 OpenAPI |
| `unconfigured` | 该角色未配置 Token，未发网络请求 |
| `auth_rejected` | 网关返回 401/403 |
| `http_error` | 网关返回其他非 200 状态（含不跟随的重定向） |
| `invalid_response` | JSON/结构无效或响应超过检查上限 |
| `unreachable` | DNS、连接、TLS 或超时等失败 |

探测只读取 OpenAPI，不封禁或执行其他游戏操作。它证明的是**面板服务器到网关**的可达性和 Token 鉴权；不能证明机器人主机也可达、上游游戏 RCON 正常或封禁可执行。

## 12. 只读验收与排查

PowerShell 示例，`WARDOGS_BOT_TOKEN` 由调用方安全注入（不是面板部署变量）：

```powershell
$botBase = 'https://panel.example.com:23333'
$headers = @{ Authorization = "Bearer $env:WARDOGS_BOT_TOKEN" }
Invoke-RestMethod -Uri "$botBase/api/bot/openapi.json" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/capabilities" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/players/76561190000000001/stats" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/players/76561190000000001/ranking?period=all" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/players/76561190000000001/reserved-slot" -Headers $headers
```

虚构 SteamID 用于检查格式与缺失数据，不期望有真实记录。依次检查匿名 OpenAPI 为 401、只读接口可读、只读调用 `/api/bot/players` 为 403、管理 Token 可读在线列表。不要使用真实封禁作为连通性测试。

TCP 失败时，先核对 Nginx 监听、容器端口映射、实际后端监听、系统/云防火墙、来源 IP 限制及机器人实际公网出口 IP；证书失败则核对域名、有效期、证书链与客户端 CA。401 检查环境加载和 Token，403 检查角色，404 检查网关路径，502–504 区分代理不可达与上游 RCON 错误。

本地模拟契约测试（不会连接实机）：

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/test_bot.py tests/test_bot_gateway_status.py
```

在线 `/api/bot/openapi.json` 由实际路由生成，含六个业务路径，省略自身和主账号诊断接口；框架生成的默认 422 声明及无类型响应可能不完整。[随仓库的 OpenAPI](bot-openapi.json) 同源生成后补充了能力、在线列表响应、通用错误和封禁混合响应，并按实际错误处理改为 400；客户端应同时参考本文。模拟测试通过不等于生产封禁或外部网络已经验证。
