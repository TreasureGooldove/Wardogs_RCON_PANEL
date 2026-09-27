import { http } from "@/utils/http";

export type FeatureKey =
  | "status"
  | "players"
  | "rotation"
  | "maps"
  | "experiences"
  | "lightings"
  | "kick"
  | "ban";

export type FeatureAvailability = boolean | null;
export type AdvertisedAction =
  | "unban"
  | "kill"
  | "message"
  | "changeFaction"
  | "broadcast"
  | "changeMap"
  | "setLighting"
  | "endMatch"
  | "restartMatch";

export interface CapabilitiesResponse {
  features: Record<FeatureKey, FeatureAvailability>;
  advertisedActions?: Partial<Record<AdvertisedAction, FeatureAvailability>>;
  observedAt: string | null;
  state: "available" | "stale" | "unavailable";
}

export const getCapabilities = () =>
  http.request<CapabilitiesResponse>("get", "/api/server/capabilities");
