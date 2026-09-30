import { http } from "@/utils/http";

export interface HistoryMatch {
  id: string;
  map: string | null;
  experiences: string[] | null;
  lighting: string | null;
  first_seen: string;
  last_seen: string;
  ended_seen: string | null;
  end_reason: string | null;
  scores: Array<{ name: string; score: number }> | null;
  sample_count: number;
  peak_players: number;
  player_count?: number;
}

export interface PlayerTotals {
  total_kills: number | null;
  total_deaths: number | null;
  latest_cash: number | null;
  peak_cash: number | null;
}
export interface HistoryPlayer extends PlayerTotals {
  steam_id: string;
  name: string;
  first_seen: string;
  last_seen: string;
  match_count: number;
}

export interface MatchPlayer {
  match_id: string;
  steam_id: string;
  name: string;
  faction: string | null;
  first_seen: string;
  last_seen: string;
  kills: number | null;
  deaths: number | null;
  cash: number | null;
  ping_ms: number | null;
  sample_count: number;
  map?: string | null;
  match_first_seen?: string;
  match_last_seen?: string;
  ended_seen?: string | null;
}

export interface Page<T> {
  total: number;
  items: T[];
}
export interface MatchDetail extends HistoryMatch {
  players: MatchPlayer[];
}
export interface PlayerDetail {
  steamId: string;
  name: string;
  matches: MatchPlayer[];
  totals: PlayerTotals;
}

export const getHistoryMatches = (offset = 0) =>
  http.request<Page<HistoryMatch>>("get", "/api/history/matches", {
    params: { offset }
  });
export const getHistoryMatch = (id: string) =>
  http.request<MatchDetail>(
    "get",
    `/api/history/matches/${encodeURIComponent(id)}`
  );
export const getHistoryPlayers = (search = "", offset = 0) =>
  http.request<Page<HistoryPlayer>>("get", "/api/history/players", {
    params: { search, offset }
  });
export const getHistoryPlayer = (id: string) =>
  http.request<PlayerDetail>(
    "get",
    `/api/history/players/${encodeURIComponent(id)}`
  );
