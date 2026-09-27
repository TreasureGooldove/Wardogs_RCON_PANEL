import { http } from "@/utils/http";
import type { SnapshotMeta } from "./snapshot";

export interface RotationItem {
  order: number;
  map: string;
  experiences: string[] | null;
  lighting: string | null;
  zoneAlternator: string | null;
  status: string | null;
  denied: boolean | null;
}

export interface RotationResponse extends SnapshotMeta {
  mode: "ordered" | "random" | "unknown";
  enabled: boolean | null;
  items: RotationItem[];
}

export const getRotation = () =>
  http.request<RotationResponse>("get", "/api/server/rotation");
