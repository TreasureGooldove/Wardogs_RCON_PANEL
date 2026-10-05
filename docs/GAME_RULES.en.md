# 🎛️ Faction population and weapon / equipment restrictions

[简体中文](GAME_RULES.md) | English (**AI translated**) | [Home](../README.en.md)

> 🧪 **Experimental features:** Faction population control and weapon/equipment restrictions are still being validated. If you encounter false positives, abnormal data, or execution problems, disable the affected rules promptly and report them via [GitHub Issues](https://github.com/TreasureGooldove/Wardogs_RCON_PANEL/issues) or QQ community group 1108826972. Include the panel version, rule settings, event time, and sanitized action records. Do not share passwords, tokens, or complete private logs.

Sidebar: **Faction and item restrictions**. Both automation features are disabled by default. Settings live in the panel database; `ServerSettings.ini` is not rewritten. Subusers can only view them. Enabling requires the owner to confirm automatic actions and enter their password again.

## Faction population control

- Separate limits for Lonestar, Valkyra, and Manticore; `0` means unlimited.
- Optionally enforce a maximum population difference of 1–1000, default 2.
- Configure minimum online players, violation grace period (default 10 seconds), and transfer interval (default 15 seconds).
- Transfer one player at a time, preferring recently observed arrivals. Players first seen in the same sample are ordered deterministically by SteamID, not actual join time.
- Reduce capacity excess first, then improve balance. Do not transfer to a full faction. If there is insufficient capacity, report no destination without kicking players.
- Unknown factions, missing actionable SteamIDs, unsupported transfer capability, or a disabled owner prevent transfers.

Uses existing observations: while watched, players/status approximately every 1/2 seconds; with players, 2/5 seconds; idle, 30/30 seconds, with existing network backoff. No independent player polling is added. Requires `PANEL_HISTORY_ENABLED=true`.

This corrects populations after sampling; it cannot prevent a player from joining first or guarantee compliance at every instant. Transfers may affect ongoing combat. Displayed counts are the latest observations.

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

- Each operation has a durable single-attempt receipt and panel audit. Uncertain writes are not retried.
- Timeouts or audit completion failures pause the corresponding rules. After verifying the result, the owner must confirm and save again to resume.
- Panel restarts and RCON target changes require rules to be saved and enabled again; old target revisions are not reused.
- Disabling requires owner login and a valid same-origin request, but no additional password.
- The game must advertise `PATCH /v1/players/{steamId}` for transfers and/or `POST /v1/players/{steamId}/kill` for kills before the respective feature can be enabled.
- Keep default-off settings until data and mappings are verified. Do not test live-server connectivity by killing or transferring occupied-server players.

Panel routes: `GET /api/game-rules` returns settings, source state, catalog, and recent receipts. Owner-only `PUT /api/game-rules/factions` and `PUT /api/game-rules/items` save the respective settings plus `targetRevision`; enabling also requires `acknowledgeRisk=true` and `password`. These are panel-cookie routes, not exposed by the bot HTTP gateway. Invalid parameters return `400 invalid_selection`, stale revisions `409 stale_server_target`, and subuser writes `403 permission_denied`.

## Catalog sources

Names and categories reference the [WARDOGS community helper database](https://wardogs.t0ki.cn/weapons.html), which credits [wardogs.zone](https://wardogs.zone/database). The snapshot contains 38 weapons and 137 equipment items, retaining only identifiers, names, and categories, not website images, code, prices, or damage data. The data file identifies 2026-09-04 as its update date; retrieval was 2026-10-05. Page and data dates may differ, and upstream content may change. These IDs serve selection and the extension contract; they are not guaranteed to match native RCON fields.
