import { http } from "@/utils/http";
import type { SnapshotMeta } from "./snapshot";

export type CatalogKind = "maps" | "experiences" | "lightings";

export interface CatalogItem {
  id: string;
  label: string;
}

export interface CatalogResponse extends SnapshotMeta {
  kind: CatalogKind;
  items: CatalogItem[];
}

export const getCatalog = (kind: CatalogKind) =>
  http.request<CatalogResponse>("get", `/api/server/catalog/${kind}`);
