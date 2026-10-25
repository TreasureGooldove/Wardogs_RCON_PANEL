# 🎛️ Faction population and weapon / equipment restrictions

[简体中文](GAME_RULES.md) | English (**AI translated**) | [Home](../README.en.md)

> 🧪 **Experimental features:** Faction population control and weapon/equipment restrictions are still being validated. If you encounter false positives, abnormal data, or execution problems, disable the affected rules promptly and report them via [GitHub Issues](https://github.com/TreasureGooldove/Wardogs_RCON_PANEL/issues) or QQ community group 1108826972. Include the panel version, rule settings, event time, and sanitized action records. Do not share passwords, tokens, or complete private logs.

Sidebar: **Faction and item restrictions**. Both automation features are disabled by default. Settings live in the panel database; `ServerSettings.ini` is not rewritten. Subusers can only view them. Enabling requires the owner to confirm automatic actions and enter their password again.

## Faction population control

Edits are retained as a temporary browser-session draft across navigation. They do not automatically save or enable transfers. Save rules explicitly; enabling still requires the owner password. Saved settings are read back and compared before showing success. Drafts are not restored for another target or changed baseline. Disabling automation does not discard population limits.

If the version dialog reports a new version but the page still says zero means unlimited, or lacks the 50V50/allow-players controls, hard-refresh with `Ctrl+F5`. If needed, replace both backend and `frontend/dist` using the complete Release ZIP, rebuild the panel container, and clear proxy/CDN HTML caches. The dialog now displays frontend build and backend versions separately. Old faction forms are rejected with a clear refresh message.

- Separate limits for Lonestar, Valkyra, and Manticore: `0` closes a faction; `-1` means unlimited. Each faction also has an allow-players switch.
- **50V50:** Select the closed faction and fill the draft. The other two receive 50 slots each, a maximum difference of 1, and no minimum population gate. This does not automatically enable or save automation; owner-password confirmation is retained.
- Optionally enforce a maximum population difference of 1–1000, default 2, among open factions only.
- Configure minimum online players, violation grace period (new rules default to 0 seconds), and transfer interval (new rules default to 5 seconds). Existing saved values are preserved.
- Evaluate every 5 seconds. Move players from factions exceeding capacity or the allowed population difference into an eligible less populated faction; equal-population destinations are selected randomly. Missing or unassigned factions are not treated as violations.
- Transfer one player at a time, preferring recently observed arrivals. Players first seen in the same sample are ordered deterministically by SteamID, not actual join time.
- Move players out of closed factions first, then reduce other capacity excess and improve balance. Never transfer into closed or full factions. If all open factions are full, report no destination without kicking players.
- Unknown factions, missing actionable SteamIDs, unsupported transfer capability, or a disabled owner prevent transfers.

Uses existing observations: while watched, players/status approximately every 1/2 seconds; with players, 2/5 seconds; idle, 30/30 seconds. While faction control is armed, the shared collector obtains fresh players and status every 5 seconds without starting another polling loop. Existing failure and rate-limit backoff still apply. Requires `PANEL_HISTORY_ENABLED=true`. A minimum of 0 imposes no population gate. A minimum of 20 prevents ordinary balancing below 20, but players in closed factions are still moved after the configured grace and cooldown.

Upgrade migration converts legacy unlimited `0` values to `-1` once, preserving positive limits and enabled state. New `0` settings stay closed across restarts. Refresh old browser tabs before saving. API writers must send `limitsVersion: 2` and the `X-Wardogs-Faction-Limits-Version: 2` header to prevent old forms from closing factions accidentally.

This corrects populations after sampling; it cannot prevent a player from joining first or guarantee compliance at every instant. Transfers may affect ongoing combat. Displayed counts are the latest observations.

## Automatic item catalog synchronization

The catalog reads the public data file behind [WARDOGS Helper](https://wardogs.t0ki.cn/weapons.html) at startup and every 6 hours, retrying failures after 15 minutes. The owner can sync manually, at most once per 60 seconds. The UI shows counts, sync status and the last success. Data-file and page dates are shown separately because the upstream labels may differ.

New weapons/equipment become selectable automatically; **restriction rules and exact cause mappings are never created or changed automatically**. Removed IDs remain marked for existing rules but cannot be newly selected in the UI. Network/format failures retain the last valid catalog in the panel database; first offline startup uses the bundled snapshot. The importer parses data without executing remote JavaScript and does not write game server configuration.

## Weapon enforcement by forced kill

1. Configure the existing game feed using `PANEL_FEED_TOKEN`, `PANEL_FEED_ORIGIN`, and game-side `[WDServerFeed]`. The panel's feed-setup entry shows deployment-specific details.
2. Select restricted items.
3. Choose actual received `cause` values or enter exact values. Each cause must identify only one selected item. Matching ignores case and surrounding whitespace only, with no fuzzy matching.
4. The owner confirms and enables rules. A matching new kill event triggers checks of the observed current round, map, and online target, then the official kill route.

**Detection occurs after the weapon causes a kill.** The official roster exposes no weapon, inventory, or worn-equipment list. It cannot detect merely picking up a weapon, nonlethal firing, purchases, or equipping, and does not remove items or modify the shop.

Catalog names and IDs may differ from server `cause` values. Mappings default to empty and are never guessed. Review real received causes before mapping. Empty mappings only match `itemId` from the extension below.

By default, one kill attempt per player every 30 seconds. Only newly inserted events are processed. Duplicate deliveries are not replayed, pre-enablement history is not punished, and suicides do not trigger punishment. A batch handles at most 10 players; evidence older than 10 seconds since receipt is skipped, as is incomplete data.

Official `eventTime` is game-relative time and cannot establish wall-clock freshness. Native kill events without `occurredAt` can only be checked against receipt time, observed current round, and map; delayed events may affect a respawned player. Producers can add a time-zone-aware `occurredAt` to skip events preceding activation, old events, or excessive future timestamps.

## Equipment detection: producer extension

The official API currently supplies no worn-gear or inventory list. **Equipment restrictions execute only when a trusted producer really observes and sends these extension events.** Adding an INI setting will not make the stock server emit them. A server plugin, bridge, or other controlled producer is required. Equipment is not inferred from names, balances, or kill distance.

Use `POST /api/ingest/events` and the separate game-feed Bearer, not a bot token:

```json
{
  "serverId": "example-game-instance",
  "events": [{
    "type": "itemUsed",
    "eventId": "example-use-0001",
    "matchId": "example-game-round",
    "mapName": "ExampleMap",
    "steamId": "76561190000000001",
    "itemId": "m4",
    "itemKind": "weapons",
    "occurredAt": "2026-01-01T00:00:00Z"
  }]
}
```

This fictional historical timestamp will not trigger an action. Send the actual occurrence time, instance/round identifiers consistent with the kill feed, and an exact catalog `itemId`. `itemKind` is `weapons` or `equipment` and must match the catalog; unknown IDs are rejected. Report actual use/equipping, not the catalog as if it were a player's inventory.

`occurredAt` requires a time zone. Only events after activation and within 10 seconds of receipt/processing are actionable, with a 3-second future tolerance. Unlinked rounds or stale current observations cannot authorize a kill. A repeated `eventId` within the same instance/event type is consumed once, including after settings changes and restarts.

Responses add `itemUsesAccepted` and `itemUsesDuplicates`. Existing `accepted/duplicates` still count only kill events; `ignored` counts unknown event types. **Event acceptance does not mean a kill was performed.** The latest extension receipt indicator establishes receipt only, not stock-server support or coverage of every player.

## Boundaries and records

### Emergency stop in the top bar

The owner can click **Stop RCON** and enter their password to block new RCON requests and attempt to close existing connections. Collection, automation, bot management and manual RCON operations stop; the game server is not shut down or restarted. The stopped state persists in the panel database across panel restarts and connection-settings changes. Already dispatched requests may have executed and cannot be undone. Interrupted writes with unknown results remain uncertain and are not retried automatically.

**Resume RCON** also requires the owner password. It changes the target revision, so faction, item and anti-cheat automation must be confirmed again. Other previously enabled features, such as rule announcements and warmup rewards, continue under their saved configuration after fresh sampling. Local history remains readable.

Endpoints: `GET /api/server/connection` for authenticated users; `POST /api/server/connection/stop` and `/resume` for the owner with same-origin verification and a password request body. These are not exposed through the bot HTTP gateway. Stopped upstream operations return `503 rcon_stopped`; a wrong password returns `401`, and a subuser write returns `403`.

### Automated rules

- Each operation has a durable single-attempt receipt and panel audit. Uncertain writes are not retried.
- Timeouts or audit completion failures pause the corresponding rules. After verifying the result, the owner must confirm and save again to resume.
- Panel restarts and RCON target changes require rules to be saved and enabled again; old target revisions are not reused.
- Disabling requires owner login and a valid same-origin request, but no additional password.
- The game must advertise `PATCH /v1/players/{steamId}` for transfers and/or `POST /v1/players/{steamId}/kill` for kills before the respective feature can be enabled.
- Keep default-off settings until data and mappings are verified. Do not test live-server connectivity by killing or transferring occupied-server players.

Panel routes: `GET /api/game-rules` returns settings, source state, catalog, and recent receipts. Owner-only `PUT /api/game-rules/factions` and `PUT /api/game-rules/items` save the respective settings plus `targetRevision`; enabling also requires `acknowledgeRisk=true` and `password`. These are panel-cookie routes, not exposed by the bot HTTP gateway. Invalid parameters return `400 invalid_selection`, stale revisions `409 stale_server_target`, and subuser writes `403 permission_denied`.

## One-shot team shuffle (experimental; AI translated)

Save rules with exactly two open factions first, even if automatic control stays disabled. Click **Preview team shuffle** in the 50V50 card. The panel randomly redistributes all online players as evenly as the saved capacities allow; closed factions never receive players. Incomplete rosters, insufficient capacity and populations outside 2–200 are rejected. This is random redistribution, not skill-, party- or friend-based balancing.

The preview lists names, SteamIDs, original and destination factions and expires after 60 seconds. It sends no transfer commands. Owner password confirmation starts a durable background job, with at least 0.5 seconds between writes and automatic balance paused. Transfers are not atomic, so intermediate counts may exceed final caps; native game restrictions may reject full-team swaps. No kill, kick, broadcast or configuration-file writes are added.

Each transfer rechecks a fresh roster, team membership, server revision, saved rule generation, map and available match timer. Any change, failure or timeout stops remaining work. If the game omits match time, a same-map restart cannot reliably be detected from status alone. Top-bar Stop Connection prevents further requests; it cannot undo sent commands. Results are verified against the final roster, with uncertainty shown when verification fails. Reusing a preview ID never resends it. Interrupted jobs do not resume or roll back after panel restart. The latest job is restored in the UI, with per-player receipts and panel audit.

Owner-cookie routes, excluded from the bot gateway:

- `POST /api/game-rules/factions/shuffle/preview`: `targetRevision`.
- `POST /api/game-rules/factions/shuffle/{id}/execute`: the same `targetRevision`, `acknowledgeRisk=true`, and `password`.
- `GET /api/game-rules/factions/shuffle/{id}`: `phase`, `plan`, `expires`, `error`. Phases: `preview/running/completed/stopped/uncertain`.

Errors: `400 shuffle_two_factions`, `409 shuffle_capacity`, `409 shuffle_stale`, `409 shuffle_busy`, plus existing password, permission, origin, stop-latch and capability errors. Validate in a controlled environment, not by transferring live players as a connectivity test.

## Native infantry-only mode (AI translated)

Open **Map rotation → Native infantry-only mode**. Separate counters show live rotation and draft entries containing `KOTH_InfantryOnly`; draft changes are not evidence of an active game rule.

1. Add infantry-only to the rotation. Each map is checked against the current server catalog, with at most four concurrent reads. Only explicitly supported maps receive the modifier after their existing base mode.
2. Review changed, unchanged and skipped counts and maps. Unsupported, failed/stale catalog reads and missing base modes are skipped. No default modes, lighting or zones are generated.
3. Review and apply the rotation using the existing validation, configuration revision check and password confirmation. Staging alone sends no game write.
4. The game rebuilds its rotation for the next normal map change. No match end or game restart is sent. If the new order is not adopted, verify the live response first; the panel does not restart the server automatically.

Remove infantry-only affects that modifier alone, preserving base modes, other modifiers, duplicate entries, order, lighting, zones, other fields, comments and line endings. Entries with no remaining base mode are skipped; ambiguous or malformed source is rejected. Rotation enabled state stays unchanged. New entries can also select the base mode and modifier individually.

Example: `Bakurani_KOTH_02+KOTH_InfantryOnly`. Use the current server's real base mode and existing rotation; do not overwrite the whole document with the example. Actual weapon and vehicle restrictions are implemented by the game version.

Viewers see live counters. Editing uses the existing Config permission and rotation-scoped `/api/server/config/rotation` route, with password confirmation for both adding and removing. No automatic punishment, kill or arbitrary command is added.

## Catalog sources

Names and categories reference the [WARDOGS community helper database](https://wardogs.t0ki.cn/weapons.html), which credits [wardogs.zone](https://wardogs.zone/database). The bundled snapshot contains 42 weapons and 140 equipment items, retaining only identifiers, names, and categories, not website images, code, prices, or damage data. The data file identifies 2026-10-10 as its update date; retrieval was 2026-10-25. Page and data dates may differ; consult the runtime sync status for later updates. These IDs serve selection and the extension contract; they are not guaranteed to match native RCON fields.
