import { http } from "@/utils/http";
import type { SnapshotMeta } from "./snapshot";

export interface Player {
  steamId: string | null;
  name: string;
  faction: string | null;
  kills: number | null;
  deaths: number | null;
  cash: number | null;
  pingMs: number | null;
}

export interface PlayersResponse extends SnapshotMeta {
  players: Player[];
  targetRevision: string;
}

export interface ModerationRequest {
  steamId: string;
  reason: string;
  targetRevision: string;
}

export const getPlayers = () =>
  http.request<PlayersResponse>("get", "/api/server/players");

export const kickPlayer = (request: ModerationRequest) =>
  http.request<void>("post", "/api/server/kicks", {
    data: request
  });

export const banPlayerPermanently = (request: ModerationRequest) =>
  http.request<void>("post", "/api/server/bans", {
    data: request
  });
