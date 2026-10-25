import type { FactionSettings } from "@/api/gameRules";
type StorageLike = Pick<Storage, "getItem" | "setItem" | "removeItem"> | null;
function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([key, item]) => [key, canonical(item)])
    );
  return value;
}
export const sameFactionSettings = (a: unknown, b: unknown) =>
  JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
const key = (revision: string) => `wardogs-faction-draft-v1:${revision}`;
export function rememberFactionDraft(
  storage: StorageLike,
  revision: string,
  base: FactionSettings,
  settings: FactionSettings
) {
  try {
    if (sameFactionSettings(base, settings)) storage?.removeItem(key(revision));
    else storage?.setItem(key(revision), JSON.stringify({ base, settings }));
  } catch {
    /* Browser storage is optional; server saves remain available. */
  }
}
export function restoreFactionDraft(
  storage: StorageLike,
  revision: string,
  base: FactionSettings
): FactionSettings | null {
  try {
    const saved = JSON.parse(storage?.getItem(key(revision)) ?? "null");
    const value = saved?.settings;
    if (
      !sameFactionSettings(saved?.base, base) ||
      !value ||
      value.limitsVersion !== 2 ||
      typeof value.enabled !== "boolean" ||
      typeof value.balanceEnabled !== "boolean"
    )
      return null;
    const bounds: [number, number, number][] = [
      ...["Lonestar", "Valkyra", "Manticore"].map(
        name => [value.limits?.[name], -1, 1000] as [number, number, number]
      ),
      [value.maxDifference, 1, 1000],
      [value.minimumPlayers, 0, 1000],
      [value.stableSeconds, 0, 300],
      [value.cooldownSeconds, 5, 600]
    ];
    if (
      bounds.some(
        ([number, min, max]) =>
          !Number.isInteger(number) || number < min || number > max
      )
    )
      return null;
    return value;
  } catch {
    return null;
  }
}
