export interface ConfigField {
  section: string;
  key: string;
  label: string;
  kind: "text" | "secret" | "number" | "boolean" | "url";
}

export interface ConfigFieldGroup {
  title: string;
  fields: ConfigField[];
}

export const CONFIG_FIELD_GROUPS: ConfigFieldGroup[] = [
  {
    title: "服务器与玩家",
    fields: [
      { section: "/Script/WDGame.WDGameSession", key: "ServerName", label: "服务器名称", kind: "text" },
      { section: "/Script/WDGame.WDGameSession", key: "ServerImageURL", label: "服务器图片 URL", kind: "url" },
      { section: "/Script/Engine.GameSession", key: "MaxPlayers", label: "最大玩家数", kind: "number" },
      { section: "/Script/WDGame.WDGameSession", key: "MaxReservedSlots", label: "最大预留位数", kind: "number" },
      { section: "MatchState.PreMatch.WaitingForPlayers.PlayerCount", key: "MinimumRequiredPlayers", label: "开局最低人数", kind: "number" }
    ]
  },
  {
    title: "对局规则",
    fields: [
      { section: "MatchState.Playing.KOTH", key: "ScorePeriod", label: "比分周期（秒）", kind: "number" },
      { section: "/Script/WDGame.WDGameStateSession", key: "bLockOverpopulatedTeamsConfig", label: "锁定人数过多的阵营", kind: "boolean" },
      { section: "/Script/WDGame.WDGameStateSession", key: "OverpopulatedTeamThresholdConfig", label: "阵营人数差阈值", kind: "number" }
    ]
  },
  {
    title: "玩家限制",
    fields: [
      { section: "/Script/WDGame.WDGameSession", key: "ServerMinPlayerCash", label: "最低现金", kind: "number" },
      { section: "/Script/WDGame.WDGameSession", key: "ServerMaxPlayerCash", label: "最高现金", kind: "number" },
      { section: "/Script/WDGame.WDGameSession", key: "ServerMinPlayerLevel", label: "最低等级", kind: "number" },
      { section: "/Script/WDGame.WDGameSession", key: "ServerMaxPlayerLevel", label: "最高等级", kind: "number" }
    ]
  }
];

export const CONFIG_FIELDS = CONFIG_FIELD_GROUPS.flatMap(group => group.fields);
export type ConfigPreset = { name: string; values: Record<string, string> };

function splitDocument(text: string) {
  const ending = text.includes("\r\n") ? "\r\n" : "\n";
  return { lines: text.split(/\r?\n/), ending };
}

function sectionBounds(lines: string[], section: string): [number, number] | null {
  const start = lines.findIndex(line => line.trim().toLowerCase() === `[${section}]`.toLowerCase());
  if (start < 0) return null;
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\s*\[[^\]]+\]\s*$/.test(lines[index])) {
      end = index;
      break;
    }
  }
  return [start, end];
}

function keyIndex(lines: string[], bounds: [number, number], key: string) {
  const expression = new RegExp(`^\\s*${key}\\s*=`);
  for (let index = bounds[0] + 1; index < bounds[1]; index += 1) {
    if (expression.test(lines[index])) return index;
  }
  return -1;
}

export function readConfigField(text: string, field: ConfigField): string {
  const { lines } = splitDocument(text);
  const bounds = sectionBounds(lines, field.section);
  if (!bounds) return "";
  const index = keyIndex(lines, bounds, field.key);
  if (index < 0) return "";
  const raw = lines[index].slice(lines[index].indexOf("=") + 1).trim();
  return raw.startsWith('"') && raw.endsWith('"') ? raw.slice(1, -1) : raw;
}

export function writeConfigField(text: string, field: ConfigField, value: string): string {
  if (/[\r\n\x00-\x1f]/.test(value) || value.length > 1024) {
    throw new Error("配置值不能包含换行、控制字符或超过 1024 字符");
  }
  if (field.kind === "number" && !/^(0|[1-9][0-9]{0,8})$/.test(value)) {
    throw new Error("请填写非负整数（最多 9 位）");
  }
  if (field.kind === "boolean" && !/^(True|False)$/i.test(value)) {
    throw new Error("布尔值只能为 True 或 False");
  }
  if (field.kind === "url" && value && !/^https:\/\/[^\s"?#]+(?:\/[^\s"?#]*)?$/i.test(value)) {
    throw new Error("图片地址需为不含查询参数的 HTTPS URL");
  }
  const { lines, ending } = splitDocument(text);
  const bounds = sectionBounds(lines, field.section);
  if (!bounds) throw new Error(`配置中没有 [${field.section}] 段落`);
  const index = keyIndex(lines, bounds, field.key);
  const previouslyQuoted = index >= 0 && /^\s*"/.test(lines[index].slice(lines[index].indexOf("=") + 1));
  const shouldQuote = field.kind === "url" || previouslyQuoted;
  if (shouldQuote && value.includes('"')) throw new Error("此配置值不能包含双引号");
  const encoded = shouldQuote ? `"${value}"` : value;
  const line = `${field.key}=${encoded}`;
  if (index >= 0) lines[index] = line;
  else lines.splice(bounds[1], 0, line);
  return lines.join(ending);
}

export function captureConfigPreset(name: string, text: string): ConfigPreset {
  const cleanName = name.trim();
  if (!cleanName || cleanName.length > 40) throw new Error("预设名称需为 1–40 字符");
  const values: Record<string, string> = {};
  for (const field of CONFIG_FIELDS) {
    if (field.kind === "secret") continue;
    values[`${field.section}|${field.key}`] = readConfigField(text, field);
  }
  return { name: cleanName, values };
}

export function applyConfigPreset(text: string, preset: ConfigPreset): string {
  let result = text;
  for (const field of CONFIG_FIELDS) {
    if (field.kind === "secret") continue;
    const value = preset.values[`${field.section}|${field.key}`];
    if (typeof value === "string") result = writeConfigField(result, field, value);
  }
  return result;
}
