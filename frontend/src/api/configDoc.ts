import { http } from "@/utils/http";

export interface ConfigDocument {
  consistency: {
    ok: boolean;
    reason: string | null;
    configuredBannedCount: number;
    liveBannedCount: number | null;
  };
  revision: string;
  writable: boolean;
  text: string;
  sections: unknown[];
  warnings: string[];
  targetRevision: string;
}

export interface ConfigValidation {
  ok?: boolean;
  errors?: unknown[];
  warnings?: unknown[];
  [key: string]: unknown;
}

export interface ConfigWriteResult extends ConfigValidation {
  revision?: string;
}

export interface ReservedSlots {
  writeIssue?: string;
  reservedSlots: string[];
  configuredReservedSlots: string[];
  pendingRestart: boolean | null;
  revision: string;
  writable: boolean;
  targetRevision: string;
  metadata: Record<
    string,
    { reason: string; expiresAt: string | null; status: string }
  >;
}

export const getConfigDocument = () =>
  http.request<ConfigDocument>("get", "/api/server/config");

export const validateConfigDocument = (text: string, targetRevision: string) =>
  http.request<ConfigValidation>("post", "/api/server/config/validate", {
    data: { text, targetRevision }
  });

export const saveConfigDocument = (request: {
  text: string;
  revision: string;
  targetRevision: string;
  fullApply?: boolean;
  password: string;
}) =>
  http.request<ConfigWriteResult>("put", "/api/server/config", {
    data: request
  });

export const getReservedSlots = () =>
  http.request<ReservedSlots>("get", "/api/server/reserved-slots");

export const addReservedSlot = (request: {
  steamId: string;
  revision: string;
  targetRevision: string;
  reason?: string;
  days?: number | null;
}) =>
  http.request<void>("post", "/api/server/reserved-slots", { data: request });

export const removeReservedSlot = (request: {
  steamId: string;
  revision: string;
  targetRevision: string;
}) =>
  http.request<void>("delete", "/api/server/reserved-slots", {
    data: request
  });

export const updateReservedSlotMetadata = (request: {
  steamId: string;
  reason: string;
  days: number | null;
  targetRevision: string;
}) =>
  http.request<ReservedSlots>("patch", "/api/server/reserved-slots/metadata", {
    data: request
  });
