import { http } from "@/utils/http";
import type { RulesConfig } from "./rules";

export interface Dossier {
  steamId: string;
  targetRevision: string;
  observedPlaytimeSeconds: number | null;
  aliases: { name: string; first_seen: string; last_seen: string }[];
  sessions: {
    id: string;
    started_at: string;
    last_seen: string;
    ended_at: string | null;
    observed_seconds: number;
    join_observed: number;
    end_reason: string | null;
  }[];
  annotation: {
    note: string;
    watched: boolean | number;
    updated_by?: string;
    updated_at?: string;
  };
  actions: {
    action: string;
    outcome: string;
    reason: string;
    actor: string;
    created_at: string;
  }[];
}
export interface Receipt {
  id: string;
  complete: boolean;
  items: {
    steam_id: string;
    name: string;
    outcome: string;
    error: string | null;
  }[];
}
export interface SteamRisk {
  steamId: string;
  vacBanned: boolean | null;
  vacBans: number | null;
  gameBans: number | null;
  communityBanned: boolean | null;
  economyBan: string | null;
  accountAgeDays: number | null;
  daysSinceLastBan: number | null;
  updatedAt: string;
  signals: string[];
}
export interface KillResponse {
  configured: boolean;
  total: number;
  lastReceivedAt: string | null;
  unlinkedRounds: number;
  rounds: {
    instance_id: string;
    game_match_id: string;
    map: string;
    local_match_id: string | null;
  }[];
  items: {
    id: number;
    received_at: string;
    local_match_id: string | null;
    map: string;
    event_time: number | null;
    killer_id: string | null;
    killer_name: string | null;
    victim_id: string | null;
    victim_name: string | null;
    cause: string | null;
    distance_meters: number | null;
    tags: string[] | null;
  }[];
}
export interface AlertSettings {
  windowMinutes: number;
  minimumKills: number;
  killsPerMinute: number;
  headshotPercent: number;
}
export interface KillAlerts {
  targetRevision: string;
  settings: AlertSettings;
  sampleCount: number;
  truncated: boolean;
  items: {
    steamId: string;
    name: string;
    gameMatchId: string;
    kills: number;
    knownTags: number;
    headshots: number;
    killsPerMinute: number | null;
    headshotPercent: number | null;
    reasons: string[];
  }[];
}
export interface Preview {
  total: number;
  truncated: boolean;
  commandsSent: number;
  items: {
    steamId: string;
    name: string;
    joinedAt: string;
    wouldSendAt: string;
    result: string;
    parts: string[];
  }[];
}
export interface AuditRow {
  id: number;
  created_at: string;
  actor: string;
  action: string;
  steam_id: string;
  outcome: string;
  reason: string;
  request_id: string;
}
export const getDossier = (id: string) =>
  http.request<Dossier>("get", `/api/community/players/${id}/dossier`);
export const saveAnnotation = (
  id: string,
  data: { note: string; watched: boolean; targetRevision: string }
) => http.request("put", `/api/community/players/${id}/annotation`, { data });
export const sendBatch = (data: {
  requestId: string;
  steamIds: string[];
  message: string;
  targetRevision: string;
}) => http.request<Receipt>("post", "/api/community/messages", { data });
export const getReceipt = (id: string) =>
  http.request<Receipt>("get", `/api/community/messages/${id}`);
export const getRisk = (steamIds: string[]) =>
  http.request<{ items: SteamRisk[] }>("post", "/api/steam/risk", {
    data: { steamIds }
  });
export const getKills = (params: Record<string, string | number>) =>
  http.request<KillResponse>("get", "/api/community/kills", { params });
export const getAlerts = () =>
  http.request<KillAlerts>("get", "/api/community/kill-alerts");
export const saveAlertSettings = (
  data: AlertSettings & { targetRevision: string }
) => http.request("put", "/api/community/kill-alerts/settings", { data });
export const getFeedSetup = () =>
  http.request<{ configured: boolean; url: string; token: string | null }>(
    "get",
    "/api/community/feed-setup"
  );
export const getAudit = (params: Record<string, string | number>) =>
  http.request<{ items: AuditRow[]; total: number; truncated: boolean }>(
    "get",
    "/api/community/audit",
    { params }
  );
export const previewRules = (c: RulesConfig) =>
  http.request<Preview>("post", "/api/community/rules/preview", {
    data: {
      targetRevision: c.targetRevision,
      enabled: c.enabled,
      firstText: c.firstText,
      secondText: c.secondText,
      delaySeconds: c.delaySeconds,
      gapSeconds: c.gapSeconds,
      cooldownMinutes: c.cooldownMinutes,
      maxPerRound: c.maxPerRound
    }
  });
export function downloadText(content: string, filename: string, mime: string) {
  const url = URL.createObjectURL(new Blob([content], { type: mime }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
