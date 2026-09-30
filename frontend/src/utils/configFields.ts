export interface ConfigField {
  section: string;
  key: string;
  label: string;
  kind: "text" | "secret" | "number" | "boolean" | "url";
}

export interface ConfigFieldGroup {
  title: string;
  section?: string;
  fields: ConfigField[];
}

const SECTION_LABELS: Record<string, string> = {
  "/script/wdgame.wdgamesession": "服务器与玩家",
  "matchstate.prematch.waitingforplayers.playercount": "开局人数条件",
  "matchstate.playing.koth": "据点占领计分",
  "/script/wdgame.wdgamestatesession": "阵营人数平衡",
  "/script/wdgame.wdservermaprotationsettings": "地图轮换设置",
  "/script/wdrcon.wdrconsettings": "RCON 连接设置",
  "/script/engine.gamesession": "玩家容量"
};

function fieldLabel(section: string, key: string): string {
  const id = section.toLowerCase();
  const name = key.toLowerCase();
  if (id === "/script/wdgame.wdservermaprotationsettings") {
    if (name === "benabled") return "启用地图轮换";
    if (name === "rotationmode") return "地图轮换方式";
  }
  if (id === "/script/wdrcon.wdrconsettings") {
    return (
      (
        {
          benabled: "启用 RCON",
          password: "RCON 密码",
          bindaddress: "监听地址",
          port: "RCON 端口"
        } as Record<string, string>
      )[name] ?? key
    );
  }
  return name === "serverpassword" ? "加入密码" : key;
}

/** Explanations only: keep the actual server identifier as the editable value. */
export function configValueLabel(
  field: ConfigField,
  value: string
): string | null {
  if (
    field.section.toLowerCase() ===
      "/script/wdgame.wdservermaprotationsettings" &&
    field.key.toLowerCase() === "rotationmode" &&
    value.toLowerCase() === "ordered"
  ) {
    return "按列表顺序轮换";
  }
  return null;
}

export const CONFIG_FIELD_GROUPS: ConfigFieldGroup[] = [
  {
    title: "服务器与玩家",
    fields: [
      {
        section: "/Script/WDGame.WDGameSession",
        key: "ServerName",
        label: "服务器名称",
        kind: "text"
      },
      {
        section: "/Script/WDGame.WDGameSession",
        key: "ServerImageURL",
        label: "服务器图片 URL",
        kind: "url"
      },
      {
        section: "/Script/Engine.GameSession",
        key: "MaxPlayers",
        label: "最大玩家数",
        kind: "number"
      },
      {
        section: "/Script/WDGame.WDGameSession",
        key: "MaxReservedSlots",
        label: "最大预留位数",
        kind: "number"
      },
      {
        section: "MatchState.PreMatch.WaitingForPlayers.PlayerCount",
        key: "MinimumRequiredPlayers",
        label: "开局最低人数",
        kind: "number"
      }
    ]
  },
  {
    title: "对局规则",
    fields: [
      {
        section: "MatchState.Playing.KOTH",
        key: "ScorePeriod",
        label: "比分周期（秒）",
        kind: "number"
      },
      {
        section: "/Script/WDGame.WDGameStateSession",
        key: "bLockOverpopulatedTeamsConfig",
        label: "锁定人数过多的阵营",
        kind: "boolean"
      },
      {
        section: "/Script/WDGame.WDGameStateSession",
        key: "OverpopulatedTeamThresholdConfig",
        label: "阵营人数差阈值",
        kind: "number"
      }
    ]
  },
  {
    title: "玩家限制",
    fields: [
      {
        section: "/Script/WDGame.WDGameSession",
        key: "ServerMinPlayerCash",
        label: "最低现金",
        kind: "number"
      },
      {
        section: "/Script/WDGame.WDGameSession",
        key: "ServerMaxPlayerCash",
        label: "最高现金",
        kind: "number"
      },
      {
        section: "/Script/WDGame.WDGameSession",
        key: "ServerMinPlayerLevel",
        label: "最低等级",
        kind: "number"
      },
      {
        section: "/Script/WDGame.WDGameSession",
        key: "ServerMaxPlayerLevel",
        label: "最高等级",
        kind: "number"
      }
    ]
  }
];

export const CONFIG_FIELDS = CONFIG_FIELD_GROUPS.flatMap(group => group.fields);
export type ConfigPreset = { name: string; values: Record<string, string> };

function splitDocument(text: string) {
  const ending = text.includes("\r\n")
    ? "\r\n"
    : text.includes("\r")
      ? "\r"
      : "\n";
  return { lines: text.split(/\r\n|\n|\r/), ending };
}

function fieldIndex(lines: string[], field: ConfigField): number {
  let section = "";
  let found = -1;
  for (let index = 0; index < lines.length; index += 1) {
    const header = lines[index]
      .replace(/^\uFEFF/, "")
      .match(/^\s*\[([^\]]+)\]\s*$/);
    if (header) {
      section = header[1].trim().toLowerCase();
      continue;
    }
    const entry = lines[index].match(/^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=/);
    if (
      section === field.section.toLowerCase() &&
      entry?.[1].toLowerCase() === field.key.toLowerCase()
    ) {
      found = index;
    }
  }
  return found;
}

export function hasConfigField(text: string, field: ConfigField): boolean {
  return fieldIndex(splitDocument(text).lines, field) >= 0;
}

export function readConfigField(text: string, field: ConfigField): string {
  const { lines } = splitDocument(text);
  const index = fieldIndex(lines, field);
  if (index < 0) return "";
  const raw = lines[index].slice(lines[index].indexOf("=") + 1).trim();
  return raw.startsWith('"') && raw.endsWith('"') ? raw.slice(1, -1) : raw;
}

export function isHiddenConfigValue(value: string): boolean {
  return /^__WD_REDACTED_[A-Za-z0-9._:-]+__$/.test(value);
}

/** Discover scalar assignments from the current server document, never defaults. */
export function discoverConfigFieldGroups(text: string): ConfigFieldGroup[] {
  const groups = new Map<string, ConfigFieldGroup>();
  let section = "";
  const fields = new Map<string, ConfigField>();
  for (const line of splitDocument(text).lines) {
    const header = line.replace(/^\uFEFF/, "").match(/^\s*\[([^\]]+)\]\s*$/);
    if (header) {
      section = header[1].trim();
      continue;
    }
    const entry = line.match(/^\s*([A-Za-z_][A-Za-z0-9_.]*)\s*=(.*)$/);
    if (!section || !entry) continue;
    const key = entry[1];
    const raw = entry[2].trim();
    // Arrays/operators and structured values remain in the lossless document editor.
    if (raw.startsWith("(")) continue;
    const known = CONFIG_FIELDS.find(
      field =>
        field.section.toLowerCase() === section.toLowerCase() &&
        field.key.toLowerCase() === key.toLowerCase()
    );
    const secret =
      /password|passwd|passphrase|secret|token|bearer|credential|api[_-]?key|private[_-]?key/i.test(
        key
      ) || isHiddenConfigValue(raw);
    const kind: ConfigField["kind"] = secret
      ? "secret"
      : /^(true|false)$/i.test(raw)
        ? "boolean"
        : /^(0|[1-9][0-9]{0,8})$/.test(raw)
          ? "number"
          : "text";
    fields.set(`${section.toLowerCase()}|${key.toLowerCase()}`, {
      section,
      key,
      label: known?.label ?? fieldLabel(section, key),
      kind
    });
  }
  for (const field of fields.values()) {
    const id = field.section.toLowerCase();
    if (!groups.has(id))
      groups.set(id, {
        title: SECTION_LABELS[id] ?? field.section,
        section: field.section,
        fields: []
      });
    groups.get(id)!.fields.push(field);
  }
  return [...groups.values()];
}

export function writeConfigField(
  text: string,
  field: ConfigField,
  value: string
): string {
  if (/[\r\n\x00-\x1f]/.test(value) || value.length > 1024) {
    throw new Error("配置值不能包含换行、控制字符或超过 1024 字符");
  }
  if (field.kind === "number" && !/^(0|[1-9][0-9]{0,8})$/.test(value)) {
    throw new Error("请填写非负整数（最多 9 位）");
  }
  if (field.kind === "boolean" && !/^(True|False)$/i.test(value)) {
    throw new Error("布尔值只能为 True 或 False");
  }
  if (
    field.kind === "url" &&
    value &&
    !/^https:\/\/[^\s"?#]+(?:\/[^\s"?#]*)?$/i.test(value)
  ) {
    throw new Error("图片地址需为不含查询参数的 HTTPS URL");
  }
  const { lines, ending } = splitDocument(text);
  const index = fieldIndex(lines, field);
  if (index < 0)
    throw new Error(`服务器配置中没有 ${field.key}，不会自动新增配置项`);
  const previouslyQuoted =
    index >= 0 &&
    /^\s*"/.test(lines[index].slice(lines[index].indexOf("=") + 1));
  const shouldQuote = field.kind === "url" || previouslyQuoted;
  if (shouldQuote && value.includes('"'))
    throw new Error("此配置值不能包含双引号");
  const encoded = shouldQuote ? `"${value}"` : value;
  const separator = lines[index].indexOf("=") + 1;
  lines[index] = `${lines[index].slice(0, separator)}${encoded}`;
  return lines.join(ending);
}

export function captureConfigPreset(name: string, text: string): ConfigPreset {
  const cleanName = name.trim();
  if (!cleanName || cleanName.length > 40)
    throw new Error("预设名称需为 1–40 字符");
  const values: Record<string, string> = {};
  for (const field of CONFIG_FIELDS) {
    if (field.kind === "secret" || !hasConfigField(text, field)) continue;
    values[`${field.section}|${field.key}`] = readConfigField(text, field);
  }
  return { name: cleanName, values };
}

export function applyConfigPreset(text: string, preset: ConfigPreset): string {
  let result = text;
  for (const field of CONFIG_FIELDS) {
    if (field.kind === "secret" || !hasConfigField(text, field)) continue;
    const value = preset.values[`${field.section}|${field.key}`];
    if (typeof value === "string")
      result = writeConfigField(result, field, value);
  }
  return result;
}
