import { http } from "@/utils/http";

export interface ServerSettings {
  name: string;
  origin: string;
  hasBearer: boolean;
  allowPublicHttp: boolean;
  publicHttpRconAllowed: boolean;
  configured: boolean;
  updatedAt: string | null;
}

export interface ServerSettingsUpdate {
  name: string;
  origin: string;
  bearer?: string;
  allowPublicHttp: boolean;
}

export const getServerSettings = () =>
  http.request<ServerSettings>("get", "/api/server/settings");

export const updateServerSettings = (settings: ServerSettingsUpdate) =>
  http.request<ServerSettings>("put", "/api/server/settings", {
    data: settings
  });
