import { http } from "@/utils/http";

export interface MapOption {
  id: string;
  label: string;
}

export interface MapOptionsResponse {
  items: MapOption[];
  observedAt: string;
  stale: boolean;
  targetRevision: string;
}

export const getMapExperiences = (map: string) =>
  http.request<MapOptionsResponse>("get", "/api/server/catalog/maps/experiences", {
    params: { map }
  });

export const getMapAlternators = (map: string) =>
  http.request<MapOptionsResponse>("get", "/api/server/catalog/maps/alternators", {
    params: { map }
  });
