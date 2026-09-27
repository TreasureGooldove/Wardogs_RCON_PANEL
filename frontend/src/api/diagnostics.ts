import { http } from "@/utils/http";

export interface HealthResponse {
  reachable: boolean;
  reportedState: string | null;
  reportedHealthy: boolean | null;
  uptimeSeconds: number | null;
  connections?: { active: number | null } | null;
  gameThreadQueue?: {
    inFlight: boolean | number | null;
    depth: number | null;
    rejectedTotal: number | null;
  } | null;
  observedAt: string;
  stale: boolean;
  targetRevision: string;
}

export interface ServerIdResponse {
  serverId: string | null;
  observedAt: string;
  stale: boolean;
  targetRevision: string;
}

export interface SponsorResponse {
  imageUrl: string | null;
  hasImage: boolean;
  observedAt: string;
  stale: boolean;
  targetRevision: string;
}

export const getHealth = () =>
  http.request<HealthResponse>("get", "/api/server/health");
export const getServerId = () =>
  http.request<ServerIdResponse>("get", "/api/server/server-id");
export const getSponsor = () =>
  http.request<SponsorResponse>("get", "/api/server/sponsor");
