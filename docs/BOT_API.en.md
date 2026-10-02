# 🤖 Bot API reference

[简体中文](BOT_API.md) | English (**AI translated**) | [OpenAPI 3.1](bot-openapi.json) | [Home](../README.en.md)

Contract: `apiVersion=1`, `build=bot-api-1`. This reference follows the repository's [bot routes](../backend/app/api/bot.py), [error handlers](../backend/app/errors.py), and [gateway diagnostics](../backend/app/api/diagnostics.py). All examples are fictional; none contain working credentials or real server details.

This is the panel's API for external bots, not the game's raw RCON API. The full panel schema is available at the panel origin's `/openapi.json`; business routes still enforce panel login and their own permissions. Bots use dedicated tokens, not administrator cookies.

## 1. Origins and permissions

Both entry points use the same paths:

| Example origin | Purpose |
| --- | --- |
| `https://panel.example.com:23333/api/bot/…` | Existing HTTPS panel entry point |
| `http://panel.example.com:23334/api/bot/…` | Optional HTTP gateway exposing only bot routes |

Ports 23333 and 23334 are deployment examples and can be changed. HTTP transmits tokens and data in cleartext. Prefer HTTPS or a trusted private network; restrict source addresses if public HTTP is necessary. The browser panel continues to use its own origin, not this HTTP gateway.

Send `Authorization: Bearer <bot-token>`. Never place tokens in URLs, frontend code, group messages, or logs.

| Environment variable | Permissions | Allowed routes |
| --- | --- | --- |
| `PANEL_BOT_READ_TOKEN` | `personal:read` | Capabilities, personal statistics, ranking, personal reserved slot, protected OpenAPI |
| `PANEL_BOT_ADMIN_TOKEN` | Above plus `players:read`, `bans:write` | Also online-player lookup and bans |

Tokens identify the bot. Submitted `operator.qqId/groupId` values are audit claims, not verified QQ identity. The bot must enforce group and operator authorization and keep the management token on its server.

The panel stores no QQ bindings. Bots may store QQ → SteamID64 locally. A 17-digit format check does not prove Steam account ownership or grant ban permission. A read token can query any syntactically valid SteamID; ordinary users' access to registered personal data must be enforced by the bot.

## 2. Deployment configuration

Run this twice in a protected terminal to generate distinct tokens:

```sh
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

Put each result into the host's protected environment file. The repository's Compose configuration uses `deploy/panel.env`:

```dotenv
PANEL_BOT_READ_TOKEN=
PANEL_BOT_ADMIN_TOKEN=
# Optional; when empty, diagnostics use PANEL_PUBLIC_ORIGIN.
PANEL_BOT_PUBLIC_ORIGIN=http://panel.example.com:23334
```

Empty values disable that identity. Tokens require at least 32 ASCII characters without whitespace or control characters. Read, management, and kill-feed tokens must differ. There is no `create-bot-token` CLI command. Keep generated values out of Git; restrict Linux environment-file permissions, for example `chmod 600 deploy/panel.env`.

`PANEL_BOT_PUBLIC_ORIGIN` sets the diagnostic target; it does not create a listener. Its hostname must match `PANEL_PUBLIC_ORIGIN`; scheme and port may differ. Do not include a path, query, credentials, or trailing slash. Preserve existing keys, including `PANEL_CONFIG_KEY`; keep `PANEL_SESSION_SECURE=true` for an HTTPS panel.

From the project root, recreate only the panel container to load changed environment values:

```sh
docker compose -f deploy/compose.yaml up -d --force-recreate panel
```

`restart` alone does not reload a changed Compose environment file. This command targets the panel service only. See the [main deployment guide](../README.en.md) for initial installation and administrator setup.

### Dedicated HTTP gateway

Use the [Nginx template](../deploy/wardogs-bot-api.http.nginx.conf). Set `server_name` and `proxy_pass` to your own hostname and actual backend listener. The repository's default Compose mapping is `127.0.0.1:18000`; verify existing installations' mappings.

Install the configuration in the active system Nginx configuration directory, run `nginx -t`, and reload only after validation passes. Allow port 23334 where needed and restrict the bot's egress IP. The template proxies only `/api/bot/`, strips Cookie and Set-Cookie, and returns 404 for other paths. GET/POST are allowed (Nginx treats HEAD as GET); the body limit is 16 KiB. Panel login, configuration, and diagnostics remain unavailable through this gateway.

After reload, wait for an anonymous `/api/bot/openapi.json` request to return 401 before authenticated read-only probes. Do not use `curl -k` to bypass HTTPS verification; install a valid certificate or a CA trusted by the client.

## 3. Routes and common conventions

| Method and path | Minimum identity | Purpose |
| --- | --- | --- |
| `GET /api/bot/capabilities` | Read | Version, server identity, features, target revision |
| `GET /api/bot/players/{steamId}/stats` | Read | Recorded server statistics and current-match snapshot |
| `GET /api/bot/players/{steamId}/ranking` | Read | Recorded lifetime kill ranking |
| `GET /api/bot/players/{steamId}/reserved-slot` | Read | Runtime membership, configuration report, expiry |
| `GET /api/bot/players` | Management | Paginated online-target lookup |
| `POST /api/bot/bans` | Management | Online-player ban with durable idempotency and audit |
| `GET /api/bot/openapi.json` | Read | Protected live bot schema; no game write operation |

- GET path SteamIDs are 17-digit strings. Do not convert them to JavaScript `Number`, which loses precision.
- Times are ISO 8601 and may use UTC `Z`. Convert display time zones in the client. Unknown values are `null`, not zero.
- Responses use `Cache-Control: no-store`. `X-Request-ID` supports tracing. A generic error's `requestId` is a trace identifier, distinct from the ban body's idempotency identifier.
- Per-process limits are shared by token role: 120 read requests/minute and 10 POST requests/minute. Bots using the same role share the quota. Upstream limits also apply; `Retry-After` is not guaranteed.
- `targetRevision` is an opaque panel target revision, not an INI-file version. Obtain it again after a panel restart or target change; do not cache it for future writes.

## 4. Capabilities

Fictional response from `GET /api/bot/capabilities`:

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

`serverId` is a target-address digest; no address or password is returned. Read identities always receive `features.ban=false`. This endpoint may query upstream capabilities. An enabled feature does not guarantee current upstream availability or complete historical data.

## 5. Personal statistics

`GET /api/bot/players/76561190000000001/stats`:

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

| Field | Meaning |
| --- | --- |
| `stats.kills/deaths/matches` | Recorded cumulative kills, deaths, and observed matches on this server; not official career data |
| `stats.cash/peakCash` | Latest / highest observed balance; not total income |
| `wins/playtimeSeconds/battleLevel` | No reliable source currently; returned as `null` |
| `currentMatch` | Player snapshot or `null`; individual values may also be `null` |
| `online` | `true` online, `false` absent from a fresh list, `null` when the list is stale |
| `updatedAt` | Latest historical observation for the player, or `null` |
| `onlineObservedAt` | Online-list sampling time, not request time |

When `online=null`, `currentMatch` may still contain cached data and must not be described as live. Unrecorded lifetime values are `null`, not zero. A player without records does not cause a “player not found” error. Statistics cover data recorded since panel collection began; short connections and events between samples can be missed. Bot-generated scores must disclose their metrics and scope; do not invent battle levels, win rates, or playtime.

## 6. Ranking

`GET /api/bot/players/76561190000000001/ranking?period=all`:

```json
{
  "steamId": "76561190000000001", "period": "all", "rank": 2, "totalPlayers": 3,
  "battleLevel": null, "rankBy": "kills", "kills": 120, "ties": "dense_rank",
  "includesOffline": true, "scope": "recorded_lifetime", "updatedAt": "2026-01-01T00:00:00Z"
}
```

Only `period=all` is supported and is the default; other values return 400. Ranking uses descending recorded lifetime kills on this server, includes offline players, and excludes unknown kill counts. Dense ranking means kills `150,150,120` receive ranks `1,1,2`. An unrecorded player has `rank/kills=null`; `totalPlayers` still counts eligible players. `updatedAt` is the server's latest match observation time.

## 7. Personal reserved slot

`GET /api/bot/players/76561190000000001/reserved-slot`:

```json
{
  "steamId": "76561190000000001", "active": false, "configured": true, "pendingRestart": true,
  "expiresAt": "2026-01-02T00:00:00Z", "reason": "Example reward", "metadataStatus": "active",
  "updatedAt": "2026-01-01T00:00:00Z", "configuredSource": "rcon_config", "configurationVerified": false
}
```

- `active`: membership in the official runtime reserved-slot list.
- `configured`: membership reported by official `/v1/config`. An unsupported configuration route yields `null`; other read failures return errors.
- `pendingRestart`: inferred difference between runtime and reported configuration (`active != configured`), or `null` if unknown. This does not guarantee that a restart resolves the difference.
- `configuredSource`: `rcon_config` when configuration is available, otherwise `null`. `configurationVerified=false` means the official report is not proven to match the disk file.
- `expiresAt/reason/metadataStatus`: panel-managed expiry, reason, and status; `null` if unrecorded. A missing expiry alone does not prove permanent-slot ownership.

Only the requested player's information is returned, not the whole list or configuration passwords. Expiry metadata does not establish runtime removal or activation. Combine it with `active`; when needed, explain that configuration reports the addition but the runtime list does not yet show it.

## 8. Administrative target lookup

`GET /api/bot/players?search=Example&limit=20&offset=0`, management token only:

| Parameter | Default | Constraint |
| --- | --- | --- |
| `search` | Empty | Maximum 64 characters; case-insensitive name substring or SteamID substring |
| `limit` | 20 | 1–50 |
| `offset` | 0 | 0–10000 |

```json
{
  "items": [{"steamId": "76561190000000001", "name": "Example Player"}],
  "total": 1, "limit": 20, "offset": 0, "targetRevision": "example-target-revision",
  "stale": false, "updatedAt": "2026-01-01T00:00:00Z", "scope": "online"
}
```

`total` counts filtered results. Empty searches can paginate the online list but still require management authentication; this is not a public list. `stale=true` denotes cached data and cannot establish current presence. Have the administrator choose the exact SteamID; do not punish an ambiguous nickname.

## 9. Bans and idempotency

`POST /api/bot/bans`, management token, `Content-Type: application/json`. Only online players are supported. There are no bot unban, offline configuration-ban, or ban-list endpoints.

```json
{
  "steamId": "76561190000000001", "reason": "Example moderation reason",
  "targetRevision": "example-target-revision", "requestId": "bot-example-operation-0001",
  "operator": {"qqId": "10000", "groupId": "20000"}
}
```

| Field | Validation |
| --- | --- |
| `steamId` | 17-digit string, first digit nonzero |
| `reason` | 1–200 characters, not all whitespace, no control characters; surrounding whitespace is stripped |
| `targetRevision` | 1–128 characters; use a freshly obtained revision |
| `requestId` | 8–128 characters, letters, digits, `_ . : -` only; unique per operation |
| `operator.qqId/groupId` | 1–20 digit strings; operator and group |

The request and `operator` object reject extra fields. The panel records operator, group, target, reason, and outcome separately from panel-administrator identity.

```json
{
  "requestId": "bot-example-operation-0001", "steamId": "76561190000000001",
  "outcome": "accepted", "code": "accepted", "replayed": false, "updatedAt": "2026-01-01T00:00:00Z"
}
```

| Outcome | Initial HTTP | Replay HTTP | Client action |
| --- | --- | --- | --- |
| `accepted` | 200 | 200 | RCON accepted the request; disk persistence is not verified |
| `rejected` | 409 | 409 | Inspect the reason and stop; rejection may arise from revision, permission, presence, or upstream checks |
| `uncertain` | 502 | 409 | Stop automatic retries; an administrator must verify bans and audit records |

Replaying the same `requestId` and normalized body returns the stored result without executing again, with `replayed=true`. Changing reason, revision, operator, or other content while reusing the identifier returns generic `409 bot_request_conflict`. Claims persist in the panel database across process restarts and token rotation; preserve the database and idempotency records. A concurrent or interrupted operation may replay as `uncertain / operation_in_progress_or_interrupted`; `updatedAt` can be omitted or `null`.

Suggested workflow: verify QQ administrator → obtain capabilities and online target → confirm player and reason → generate and save a unique identifier and full body → submit once → display the outcome. Refresh the target after `stale_server_target`; stop on `player_not_online`. For codes including `action_uncertain`, `internal_error`, and `audit_completion_failed`, interpret `outcome`, not only HTTP status.

After a connection failure or timeout, do not retry with a new identifier or claim success. Replaying the exact original body may retrieve its receipt, but does not force re-execution or complete an unresolved operation. A player going offline does not prove a ban succeeded. There is no separate operation-status endpoint; administrators verify the actual ban list in the panel or original RCON client.

Authentication, validation, and idempotency conflicts can return generic errors instead of ban receipts. Clients must support both response structures.

## 10. Errors

Generic example:

```json
{"code": "bot_invalid_request", "message": "机器人请求参数无效", "requestId": "example-trace-id"}
```

| HTTP | Generic code | Meaning |
| --- | --- | --- |
| 400 | `bot_invalid_request` | Invalid path, query, or body; actual validation status is not FastAPI's default 422 |
| 401 | `not_authenticated` | Missing, incorrect, or disabled token; bots do not need cookie login |
| 403 | `permission_denied` | Read token attempting a management route |
| 409 | `bot_request_conflict` | Identifier already claimed for different content |
| 429 | `rate_limited`, `rcon_rate_limited` | Panel or upstream rate limit |
| 501 | `route_unsupported`, `action_unsupported` | Server does not advertise the capability |
| 502 | `rcon_auth_failed`, `invalid_upstream` | Upstream authentication or parsing failure |
| 503 | `rcon_unconfigured`, `rcon_unavailable` | Target unconfigured or unreachable |
| 504 | `rcon_timeout` | Upstream read timeout |

Errors after a ban claim are embedded in a receipt, with status mapping from the previous section. Receipt codes also include `write_disabled`, `stale_server_target`, `player_not_online`, `action_rejected`, `action_uncertain`, `internal_error`, and `audit_completion_failed`. Messages may be Chinese; parse `code/outcome`, not message text. Unhandled internal failures may return 500 without the generic JSON structure.

Nginx 404, method-denied 403, or oversized-body 413 responses may be HTML. DNS, TCP, and TLS failures have no API JSON. Check status and content type before parsing.

## 11. API status in the panel

Log in as the owner → **Server diagnostics → Bot API status**, then refresh the probe.

The underlying `GET /api/server/bot-api-status` requires the owner's panel cookie, **not a bot token**, and is unavailable through port 23334. It returns `apiVersion`, `panelVersion`, `gatewayOrigin`, `encrypted`, `observedAt`, `probeSource=panel_server`, `probePath=/api/bot/openapi.json`, and a `credentials` array. Each item has `role`, `configured`, `permissions`, `state`, `httpStatus`, and `latencyMs`; no credentials are returned.

| `state` | Meaning |
| --- | --- |
| `available` | Authenticated schema fetch and structural check succeeded for this role |
| `unconfigured` | Token not configured; no network request made |
| `auth_rejected` | Gateway returned 401/403 |
| `http_error` | Other non-200 status, including redirects that are not followed |
| `invalid_response` | Invalid JSON/structure or response above the probe size limit |
| `unreachable` | DNS, connection, TLS, or timeout failure |

Probes only read OpenAPI and never perform game writes. They establish **panel-server-to-gateway** reachability and authentication, not external bot reachability, upstream RCON health, or successful ban execution.

## 12. Read-only acceptance checks and troubleshooting

PowerShell example: securely inject `WARDOGS_BOT_TOKEN` into the caller's environment; it is not a panel deployment variable.

```powershell
$botBase = 'https://panel.example.com:23333'
$headers = @{ Authorization = "Bearer $env:WARDOGS_BOT_TOKEN" }
Invoke-RestMethod -Uri "$botBase/api/bot/openapi.json" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/capabilities" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/players/76561190000000001/stats" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/players/76561190000000001/ranking?period=all" -Headers $headers
Invoke-RestMethod -Uri "$botBase/api/bot/players/76561190000000001/reserved-slot" -Headers $headers
```

The fictional SteamID checks syntax and missing-data behavior, not real records. Check anonymous OpenAPI returns 401, read routes are available, read-token `/api/bot/players` returns 403, and the management token can read the online list. Do not test connectivity using a real ban.

For TCP failures, check Nginx listeners, container mappings, actual backend listeners, host/cloud firewalls, source restrictions, and the bot's real public egress IP. For certificate errors, check hostname, expiry, certificate chain, and client CA trust. For 401 check loaded environment and token; for 403 check role; for 404 check gateway path; for 502–504 distinguish proxy connectivity from upstream RCON errors.

Local mocked contract tests (no production connection):

```powershell
Set-Location backend
.\.venv\Scripts\python.exe -m pytest tests/test_bot.py tests/test_bot_gateway_status.py
```

Live `/api/bot/openapi.json` is route-generated and contains six business paths; it omits itself and owner diagnostics. Framework default 422 declarations and untyped responses may be incomplete. The [repository OpenAPI](bot-openapi.json) uses the same route generator with documented supplements for capabilities, online-list responses, generic errors, and mixed ban responses, replacing 422 with actual 400 handling. Read this reference alongside the schema. Passing mocked tests does not establish production ban execution or external network availability.
