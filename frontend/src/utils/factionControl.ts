import type { FactionName, FactionSettings } from "@/api/gameRules";

/** A local draft only. Enabling/saving still uses the existing owner confirmation. */
export function fiftyVsFiftyDraft(
  current: FactionSettings,
  closed: FactionName
): FactionSettings {
  const names: FactionName[] = ["Lonestar", "Valkyra", "Manticore"];
  if (!names.includes(closed)) throw new Error("invalid faction");
  return {
    ...current,
    limitsVersion: 2,
    limits: Object.fromEntries(
      names.map(name => [name, name === closed ? 0 : 50])
    ) as Record<FactionName, number>,
    balanceEnabled: true,
    maxDifference: 1,
    minimumPlayers: 0
  };
}
