# Wardogs RCON 管理面板

简体中文 | [English](README.en.md)

> ⚠️ **项目仍在开发中。使用前请务必备份服务器配置与数据，并在可控环境中验证写操作。**

> 🤖 **AI 友好**：如果您不会部署，可以丢给 DeepSeek Harness 进行安装。

面向 Wardogs 专用服务器的自托管管理面板。前端使用 Vue 3、TypeScript 和 Element Plus，后端使用 FastAPI 与 SQLite。浏览器只连接面板 API；RCON 凭据留在服务端。

> 💬 **社区群**：WarDogs战狗 超级猫猫服务器社区群 · QQ 群号 **1108826972**。欢迎玩家和服务器管理员交流。

> 当前版本管理一台服务器。它调用 Wardogs HTTP RCON `/v1` 路由，不提供任意控制台命令执行入口。

## ✨ 功能

- **服务器与玩家**：查看状态、地图、比分、在线玩家；玩家按 Lonestar、Valkyra、Manticore 分栏，页面可见时约每 5 秒刷新。
- **管理操作**：踢出、永久封禁与解封、击杀角色、私聊、人工警告、切换阵营、全服公告、地图与光照调整、结束或重开比赛。可用操作取决于服务器公告的 RCON 能力；危险操作需要确认。
- **名单与预留位**：读取封禁列表和服务器预留位；预留原因、期限只保存在面板，期限到达后由面板移除对应预留位。写入结果不确定时停止自动重试，等待人工核查。
- **暖服奖励**：人数持续回落后重新达标，并同时满足每天最多一次和间隔小时数，才自动赠送预留位；支持可编辑通知、私聊或全服公告，已有更长的预留期限不会缩短。默认关闭。
- **版本更新**：登录后自动读取本仓库最新 GitHub Release；导航栏可手动检查、查看当前版本和发布说明。检查结果有缓存，升级需由管理员执行。
- **配置草稿**：从服务器读取配置后在浏览器编辑；应用前校验草稿并再次输入当前账号密码。草稿、预设和验证不会直接改动服务器配置。
- **官方配置接口异常保护**：部分服务端的 `/v1/config` 会遗漏封禁数组。面板核对独立的 `/v1/bans`；不一致或无法核对时标注“官方接口存在问题”，禁用配置编辑、验证、下载和应用，并拦截预留位、暖服奖励等相关整份配置写入。不会自行补造缺失配置。
- **协作与记录**：创建子用户、生成随机密码、逐项授权；默认只读。面板按采样记录历史玩家和对局，并提供服规私聊播报及本地发送记录。服规自动播报默认关闭。
- **反作弊（实验性）**：独立侧栏入口，提供近距离组合、区域聚集组合、短时连续击杀、高爆头比例和远距离连续爆头规则。系统和每条规则均默认关闭；主账号保存启用设置必须确认风险并重新输入密码。处理方式为仅提示、踢出或封禁，异常阈值不等于作弊证据，建议先仅提示并人工复核。
- **繁体中文**：语言菜单可切换繁體中文，覆盖面板与反作弊页面。

反作弊只处理启用后收到的事件，同一玩家同一游戏实例/对局最多自动处罚一次，结果不确定不重试。数据不足、目标版本变化或主账号被停用时不执行处罚。射击距离沿用官方 `distance`（厘米），显示和规则阈值使用米；区域聚集另需每条击杀事件携带 `victimPositionMeters: {"x": 0, "y": 0, "z": 0}`（真实世界坐标，米），且受害者位置两两距离不超过阈值。未提供坐标时该规则不判定，不从射击距离推算位置。现有游戏推送若没有位置字段，需要在事件生产端补充；面板不会自动获得不存在的数据。

这些规则不是客户端反作弊检测，只检查事件统计。延迟上报、服务器事件质量、武器和爆炸多杀都可能影响结果；接收时间的新鲜度无法证明源事件真实发生时间。仅分析近期事件、限定样本量，超限时不处罚。

历史对局来自定时快照，短暂上线和两次采样之间的事件可能遗漏；面板不会补录启用之前的历史。

## 🖼️ 界面预览

以下为开发期间的界面截图，实际页面可能随版本调整。在线玩家截图中的玩家姓名与 SteamID 已在原图中模糊处理。

**三阵营在线玩家**

![按三个阵营分栏的在线玩家](doc/PixPin_2026-09-27_21-54-30.jpg)

**服规播报**

![服规播报设置与状态](doc/PixPin_2026-09-27_21-55-59.jpg)

**管理操作**

![公告、比赛控制与地图切换](doc/PixPin_2026-09-27_21-56-10.jpg)

## 🚀 本地启动

需要 Python 3.11+、Node.js 和 pnpm 9。以下命令在 PowerShell 中，从准备存放仓库的目录执行：

```powershell
git clone https://github.com/TreasureGooldove/Wardogs_RCON_PANEL.git
Set-Location Wardogs_RCON_PANEL\backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e '.[dev]'
$env:PANEL_PUBLIC_ORIGIN = 'http://127.0.0.1:8848'
.\.venv\Scripts\python.exe -m app.cli create-admin
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

在另一个 PowerShell 窗口中，从同一个父目录启动前端：

```powershell
Set-Location Wardogs_RCON_PANEL\frontend
pnpm install --frozen-lockfile
pnpm dev
```

打开 `http://127.0.0.1:8848`。如需先体验只读模拟数据，可在启动后端前、同一 PowerShell 窗口中设置 `WARDOGS_RCON_ORIGIN=http://127.0.0.1:18765`、`WARDOGS_RCON_BEARER=local-mock-only`、`WARDOGS_ALLOW_PRIVATE_HTTP=true`，并在另一个窗口的 `backend/` 目录运行 `python -m tests.mock_rcon`。这些值只用于本地模拟。

保存真实服务器连接前，应在后端环境中设置持久的 `PANEL_CONFIG_KEY`（Fernet 密钥）；生成方法及其他环境变量见 [后端示例](backend/.env.example)。不要把真实 Bearer、Steam Web API Key、管理员密码或数据库放进 Git。服务端环境变量不会因复制 `.env.example` 而自动加载；Docker Compose 使用 [部署环境文件](deploy/panel.env.example)。

## 🌐 多语言

支持简体中文、English、日本語和 한국어。登录页、顶部导航及“界面设置”均可切换语言，选择保存在当前浏览器。

除中文外，其他语言均标注 **AI 翻译**，仅供参考。语言包随应用发布，运行时不请求翻译服务；服务器名称、玩家昵称、SteamID、RCON 命令标识及用户编辑的公告/服规保持原文。切换语言前请保存未提交的草稿，确认后界面会重新载入。

## 🛠️ 部署与检查

📊 历史玩家列表及详情展示本服已记录的累计击杀、死亡、K/D、最近现金和最高现金。重复采样不重复累加；旧数据仅按已有快照回填，不能恢复采集前的数据，现金不作为累计收入。

🔄 支持 GitHub / [Gitee 国内源](https://gitee.com/gooldove/Wardogs_RCON_PANEL/releases) 检查与安装更新；可启用后台自动更新（默认关闭，管理员密码确认）。Release 为空时使用带 SHA-256 的预构建更新清单；安装失败尝试回退。需要按部署教程安装宿主机更新服务。

部署模板和步骤见 [deploy/README.md](deploy/README.md)。支持 HTTP 与 HTTPS 部署；HTTP 访问在进入登录页面前显示安全提醒。教程包含 HTTP 配置、HTTPS 自动证书及迁移步骤，建议使用 HTTPS。公网明文 HTTP RCON 需要显式启用双重门禁，并会明文传输具有管理权限的 Bearer，优先使用 HTTPS 或受控私网。

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests -q
Set-Location ..\frontend
pnpm run typecheck
pnpm run build
```

真实服有人在线时，只做必要的只读查询；踢出、封禁、警告、比赛控制和配置写入测试应安排在可控时段。RCON 写操作不会因超时自动重试。

## 🙏 项目来源与许可

- [vue-pure-admin](https://github.com/pure-admin/vue-pure-admin) 提供管理界面的上游技术体系；本项目实际以前端精简版 [pure-admin-thin](https://github.com/pure-admin/pure-admin-thin) 为基础进行改造。固定来源提交及保留的 MIT 许可见 [frontend/UPSTREAM.md](frontend/UPSTREAM.md) 和 [frontend/LICENSE](frontend/LICENSE)。
- [Warcon](https://github.com/warcon-app/warcon) 是 Wardogs RCON 面板的功能参考，启发了历史、协作管理等需求。本项目为独立实现，采用不同的前后端和存储结构；所列功能不代表与 Warcon 完全一致。

仓库根目录保留原仓库的 [Apache-2.0 许可证](LICENSE)；前端上游代码继续遵守其 MIT 许可。
