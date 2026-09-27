import { http } from "@/utils/http";
import type { SnapshotMeta } from "./snapshot";

export interface FactionScore {
  name: string;
  score: number;
}

export interface StatusResponse extends SnapshotMeta {
  serverName: string | null;
  map: string | null;
  experiences: string[] | null;
  lighting: string | null;
  alternator: string | null;
  playerCount: number | null;
  maxPlayers: number | null;
  factionScores: FactionScore[] | null;
  scoreTick: { current: number | null; min: number | null; max: number | null } | null;
  scoreCap: number | null;
  matchSeconds: number | null;
  rotation: { nowIndex: number | null; nextIndex: number | null } | null;
}

export const getServerStatus = () =>
  http.request<StatusResponse>("get", "/api/server/status");
