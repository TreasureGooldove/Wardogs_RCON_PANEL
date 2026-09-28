import { http } from "@/utils/http";

export interface WarmupTarget {
  steam_id: string;
  player_name: string;
  action: "new" | "renew";
  expires_at: string;
  notification_status: string;
}

export interface WarmupRun {
  id: string;
  started_at: string;
  finished_at: string | null;
  outcome: string;
  player_count: number;
  gift_days: number;
  awarded_count: number;
  skipped_count: number;
  notification_mode: "private" | "broadcast";
  notification_status: string;
  detail: string;
  targets: WarmupTarget[];
}

export interface WarmupState {
  enabled: boolean;
  playerThreshold: number;
  giftDays: number;
  intervalMode: "daily" | "hours";
  intervalHours: number;
  notificationMode: "private" | "broadcast";
  targetRevision: string;
  collectorEnabled: boolean;
  configured: boolean;
  observedPlayers: number | null;
  nextDetectionAt: string | null;
  attentionRequired: boolean;
  runs: WarmupRun[];
}

export type WarmupUpdate = Pick<
  WarmupState,
  | "enabled"
  | "playerThreshold"
  | "giftDays"
  | "intervalMode"
  | "intervalHours"
  | "notificationMode"
  | "targetRevision"
> & { password?: string };

export const getWarmup = () => http.request<WarmupState>("get", "/api/warmup");

export const saveWarmup = (data: WarmupUpdate) =>
  http.request<WarmupState>("put", "/api/warmup", { data });

export const acknowledgeWarmup = (
  runId: string,
  data: { targetRevision: string; password: string }
) =>
  http.request<WarmupState>("post", `/api/warmup/runs/${runId}/acknowledge`, {
    data
  });
