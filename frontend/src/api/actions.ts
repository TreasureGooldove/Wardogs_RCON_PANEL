import { http } from "@/utils/http";
import type { SnapshotMeta } from "./snapshot";
import type { CatalogItem } from "./catalog";

export interface BannedPlayer {
  steamId: string;
  bannedAtUtc: string | null;
  bannedBy: string | null;
  reason: string | null;
  source?: "config";
}

export interface BansResponse extends SnapshotMeta {
  bans: BannedPlayer[];
  targetRevision: string;
}

export interface AuditEntry {
  timestampUtc: string | null;
  peer: string | null;
  sessionId: string | null;
  event: string | null;
  detail: string | null;
}

export interface AuditResponse extends SnapshotMeta {
  entries: AuditEntry[];
  targetRevision: string;
}

export type AuditLimit = 25 | 50 | 100 | 200;

export interface ActionResult {
  ok: boolean;
  respawned?: boolean | null;
  respawnUncertain?: boolean;
  respawnError?: string;
}

export interface TargetRequest {
  targetRevision: string;
}

export interface PlayerActionRequest extends TargetRequest {
  steamId: string;
}

export interface MessageRequest extends PlayerActionRequest {
  message: string;
}

export interface FactionRequest extends PlayerActionRequest {
  faction: string;
  respawn?: boolean;
}

export interface MapRequest extends TargetRequest {
  map: string;
  experiences?: string[];
  lighting?: string;
  zoneAlternator?: string;
}

export interface MapExperiencesResponse extends SnapshotMeta {
  experiences: string[];
  items: CatalogItem[];
  targetRevision: string;
}

export interface MapAlternatorsResponse extends SnapshotMeta {
  alternators: { tag: string; displayName: string }[];
  items: CatalogItem[];
  targetRevision: string;
}

export const getBans = () =>
  http.request<BansResponse>("get", "/api/server/bans");

export const getAudit = (limit: AuditLimit) =>
  http.request<AuditResponse>("get", "/api/server/audit", {
    params: { limit }
  });

export const getMapExperiences = (map: string) =>
  http.request<MapExperiencesResponse>(
    "get",
    "/api/server/catalog/maps/experiences",
    { params: { map } }
  );

export const getMapAlternators = (map: string) =>
  http.request<MapAlternatorsResponse>(
    "get",
    "/api/server/catalog/maps/alternators",
    { params: { map } }
  );

export const unbanPlayer = (request: PlayerActionRequest) =>
  http.request<ActionResult>("post", "/api/server/unbans", { data: request });

export const killPlayer = (request: PlayerActionRequest) =>
  http.request<ActionResult>("post", "/api/server/kills", { data: request });

export const messagePlayer = (request: MessageRequest) =>
  http.request<ActionResult>("post", "/api/server/messages", { data: request });

export const changePlayerFaction = (request: FactionRequest) =>
  http.request<ActionResult>("post", "/api/server/factions", { data: request });

export const broadcastMessage = (
  request: TargetRequest & { message: string }
) =>
  http.request<ActionResult>("post", "/api/server/broadcast", {
    data: request
  });

export const endMatch = (request: TargetRequest) =>
  http.request<ActionResult>("post", "/api/server/match/end", {
    data: request
  });

export const restartMatch = (request: TargetRequest) =>
  http.request<ActionResult>("post", "/api/server/match/restart", {
    data: request
  });

export const changeMap = (request: MapRequest) =>
  http.request<ActionResult>("post", "/api/server/match/map", {
    data: request
  });

export const setLighting = (request: TargetRequest & { lighting: string }) =>
  http.request<ActionResult>("put", "/api/server/world/lighting", {
    data: request
  });
