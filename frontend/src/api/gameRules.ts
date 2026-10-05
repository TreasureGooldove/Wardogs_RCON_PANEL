import { http } from "@/utils/http";

export type FactionName = "Lonestar" | "Valkyra" | "Manticore";
export interface FactionSettings {
  enabled: boolean;
  limits: Record<FactionName, number>;
  balanceEnabled: boolean;
  maxDifference: number;
  minimumPlayers: number;
  stableSeconds: number;
  cooldownSeconds: number;
}
export interface RestrictedItem {
  itemId: string;
  killCauses: string[];
}
export interface ItemSettings {
  enabled: boolean;
  items: RestrictedItem[];
  cooldownSeconds: number;
}
export interface RuleReceipt {
  id: string;
  steam_id: string;
  details: {
    from?: string;
    to?: string;
    itemId?: string;
    cause?: string;
    source?: string;
  };
  outcome: string;
  error: string | null;
  created_at: string;
}
interface RuleView<T> {
  settings: T;
  active: boolean;
  blocked: boolean;
  requiresRearm: boolean;
  receipts: RuleReceipt[];
}
export interface CatalogItem {
  id: string;
  name: string;
  englishName: string;
  kind: "weapons" | "equipment";
  category: string;
}
export interface GameRulesView {
  targetRevision: string;
  factions: RuleView<FactionSettings>;
  items: RuleView<ItemSettings>;
  observation: {
    counts: Record<FactionName, number>;
    state: string;
    observedAt: string | null;
  };
  evidence: {
    causes: { cause: string; samples: number; lastReceivedAt: string }[];
    lastItemUsedAt: string | null;
  };
  historyEnabled: boolean;
  feedConfigured: boolean;
  equipmentInventoryAvailable: boolean;
  catalog: {
    source: string;
    upstreamSource: string;
    sourceUpdatedAt: string;
    retrievedAt: string;
    items: CatalogItem[];
  };
}
export const getGameRules = () =>
  http.request<GameRulesView>("get", "/api/game-rules");
type Confirmation = {
  targetRevision: string;
  acknowledgeRisk: boolean;
  password?: string;
};
export const saveFactionRules = (data: FactionSettings & Confirmation) =>
  http.request("put", "/api/game-rules/factions", { data });
export const saveItemRules = (data: ItemSettings & Confirmation) =>
  http.request("put", "/api/game-rules/items", { data });
