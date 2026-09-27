import { http } from "@/utils/http";

export interface RulesConfig {
  configuredOrigin: string;
  currentOrigin: string;
  enabled: boolean;
  firstText: string;
  secondText: string;
  delaySeconds: number;
  gapSeconds: number;
  cooldownMinutes: number;
  maxPerRound: number;
  updatedAt: string;
  targetRevision: string;
  collectorEnabled: boolean;
  pending: number;
  baselineReady: boolean;
  onlineObserved: number;
  lastPlayerSnapshotFresh: boolean;
}

export interface RulesDelivery {
  id: string;
  steam_id: string;
  player_name: string;
  stage: number;
  part: number;
  outcome: string;
  created_at: string;
  finished_at: string | null;
  error_code: string | null;
}

export const getRules = () => http.request<RulesConfig>("get", "/api/rules");
export const saveRules = (config: RulesConfig) =>
  http.request<RulesConfig>("put", "/api/rules", {
    data: {
      targetRevision: config.targetRevision,
      enabled: config.enabled,
      firstText: config.firstText,
      secondText: config.secondText,
      delaySeconds: config.delaySeconds,
      gapSeconds: config.gapSeconds,
      cooldownMinutes: config.cooldownMinutes,
      maxPerRound: config.maxPerRound
    }
  });
export const getRulesDeliveries = () =>
  http.request<{ items: RulesDelivery[] }>("get", "/api/rules/deliveries");
export const sendRulesManually = (steamId: string, targetRevision: string) =>
  http.request<{ firstSent: boolean; secondSent: boolean | null }>(
    "post", "/api/rules/manual", { data: { steamId, targetRevision } }
  );
