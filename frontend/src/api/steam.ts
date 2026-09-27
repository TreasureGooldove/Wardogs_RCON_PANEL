import { http } from "@/utils/http";

export interface SteamProfile {
  steamId: string;
  personaName: string | null;
  avatarUrl: string | null;
  profileUrl: string | null;
}

export interface SteamProfilesResponse {
  profiles: SteamProfile[];
}

export const getSteamProfiles = (steamIds: string[]) =>
  http.request<SteamProfilesResponse>("post", "/api/steam/profiles", {
    data: { steamIds }
  });
