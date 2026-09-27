import { http } from "@/utils/http";
import type { SnapshotMeta } from "./snapshot";

export type WarningOutcome = "accepted" | "rejected" | "uncertain";

export interface WarningEntry {
  id: number;
  reason: string;
  actor: string;
  outcome: WarningOutcome;
  createdAt: string;
}

export interface WarningHistory extends SnapshotMeta {
  steamId: string;
  count: number;
  entries: WarningEntry[];
  source: "panel_local";
  targetRevision: string;
}

export interface SendWarningRequest {
  steamId: string;
  reason: string;
  targetRevision: string;
}

export interface SendWarningResult {
  ok: boolean;
  recorded: boolean;
}

export const getWarningHistory = (steamId: string) =>
  http.request<WarningHistory>("get", "/api/server/warnings", {
    params: { steamId }
  });

export const sendWarning = (request: SendWarningRequest) =>
  http.request<SendWarningResult>("post", "/api/server/warnings", {
    data: request
  });
