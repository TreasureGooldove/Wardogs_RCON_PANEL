import { t } from "@/i18n";
export const FACTIONS = [
  { code: "BLU", name: "Lonestar", color: "#5B95D8" },
  { code: "RED", name: "Valkyra", color: "#D86060" },
  { code: "GRN", name: "Manticore", color: "#7BC462" }
] as const;

export type FactionCode = (typeof FACTIONS)[number]["code"];

export function normalizeFaction(raw: string | null): FactionCode | null {
  switch (raw?.trim().toUpperCase()) {
    case "BLU":
    case "LONESTAR":
      return "BLU";
    case "RED":
    case "VALKYRA":
      return "RED";
    case "GRN":
    case "MANTICORE":
      return "GRN";
    default:
      return null;
  }
}

export function factionDisplay(raw: string | null) {
  const code = normalizeFaction(raw);
  const faction = FACTIONS.find(item => item.code === code);
  return faction ?? { name: raw?.trim() || t("未知"), color: "#909399" };
}
