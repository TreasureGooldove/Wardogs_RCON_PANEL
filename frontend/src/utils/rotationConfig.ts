export interface RotationDraftEntry {
  map: string;
  experience: string;
  lighting: string;
  zoneAlternator: string;
  source: string;
}

export interface RotationDraft {
  enabled: boolean | null;
  mode: "ordered" | "random" | null;
  entries: RotationDraftEntry[];
}

const SECTION = "[/Script/WDGame.WDServerMapRotationSettings]";
const SAFE_ID = /^[A-Za-z0-9_.-]+$/;
export const INFANTRY_ONLY = "KOTH_InfantryOnly";

export function hasInfantryOnly(
  experiences: string[] | string | null
): boolean {
  const values =
    typeof experiences === "string" ? experiences.split("+") : experiences;
  return !!values?.some(
    value => value.trim().toLowerCase() === INFANTRY_ONLY.toLowerCase()
  );
}

function linesOf(text: string) {
  const ending = text.includes("\r\n") ? "\r\n" : "\n";
  return { lines: text.split(/\r?\n/), ending };
}

function sectionRange(lines: string[]): [number, number] {
  const start = lines.findIndex(line => line.trim() === SECTION);
  if (start < 0) throw new Error("配置中没有地图轮换段落");
  let end = lines.length;
  for (let index = start + 1; index < lines.length; index += 1) {
    if (/^\s*\[[^\]]+\]\s*$/.test(lines[index])) {
      end = index;
      break;
    }
  }
  return [start, end];
}

function entryIndexes(lines: string[], start: number, end: number) {
  const result: number[] = [];
  for (let index = start + 1; index < end; index += 1) {
    if (/^\s*[.+]RotationEntries\s*=/.test(lines[index])) result.push(index);
  }
  return result;
}

function field(line: string, key: string) {
  const expression = new RegExp(
    `(?:^|[,\\s(])${key}=(?:"([^"\\r\\n]*)"|([^,\\s)\\r\\n]*))`
  );
  const match = expression.exec(line);
  return match?.[1] ?? match?.[2] ?? "";
}

export function parseRotationDraft(text: string): RotationDraft {
  const { lines } = linesOf(text);
  const [start, end] = sectionRange(lines);
  let enabled: boolean | null = null;
  let mode: "ordered" | "random" | null = null;
  for (let index = start + 1; index < end; index += 1) {
    const enabledMatch = /^\s*bEnabled\s*=\s*(True|False)\s*$/i.exec(
      lines[index]
    );
    if (enabledMatch) enabled = enabledMatch[1].toLowerCase() === "true";
    const modeMatch = /^\s*RotationMode\s*=\s*(Ordered|Random)\s*$/i.exec(
      lines[index]
    );
    if (modeMatch) mode = modeMatch[1].toLowerCase() as "ordered" | "random";
  }
  const entries = entryIndexes(lines, start, end).map(index => {
    const source = lines[index];
    const map = field(source, "Map");
    if (!map) throw new Error("存在无法识别的地图条目，请在配置文件页编辑");
    return {
      map,
      experience: field(source, "Experience") || field(source, "Experiences"),
      lighting: field(source, "Lighting"),
      zoneAlternator: field(source, "ZoneAlternator"),
      source
    };
  });
  return { enabled, mode, entries };
}

function edit(
  text: string,
  change: (lines: string[], start: number, end: number) => void
) {
  const { lines, ending } = linesOf(text);
  const [start, end] = sectionRange(lines);
  change(lines, start, end);
  return lines.join(ending);
}

export function setRotationSetting(
  text: string,
  setting: "enabled" | "mode",
  value: boolean | "ordered" | "random"
) {
  const name = setting === "enabled" ? "bEnabled" : "RotationMode";
  const encoded =
    setting === "enabled"
      ? value === true
        ? "True"
        : "False"
      : value === "ordered"
        ? "Ordered"
        : "Random";
  return edit(text, (lines, start, end) => {
    const expression = new RegExp(`^\\s*${name}\\s*=`);
    const index = lines.findIndex(
      (line, position) =>
        position > start && position < end && expression.test(line)
    );
    if (index >= 0) lines[index] = `${name}=${encoded}`;
    else lines.splice(start + 1, 0, `${name}=${encoded}`);
  });
}

export function moveRotationEntry(text: string, from: number, to: number) {
  return edit(text, (lines, start, end) => {
    const indexes = entryIndexes(lines, start, end);
    if (from < 0 || to < 0 || from >= indexes.length || to >= indexes.length)
      throw new Error("轮换条目已经变化，请重新读取");
    [lines[indexes[from]], lines[indexes[to]]] = [
      lines[indexes[to]],
      lines[indexes[from]]
    ];
  });
}

export function removeRotationEntry(text: string, position: number) {
  return edit(text, (lines, start, end) => {
    const indexes = entryIndexes(lines, start, end);
    if (position < 0 || position >= indexes.length)
      throw new Error("轮换条目已经变化，请重新读取");
    lines.splice(indexes[position], 1);
  });
}

export function addRotationEntry(
  text: string,
  entry: Pick<
    RotationDraftEntry,
    "map" | "experience" | "lighting" | "zoneAlternator"
  >
) {
  if (
    !entry.map ||
    !SAFE_ID.test(entry.map) ||
    [entry.lighting, entry.zoneAlternator].some(
      value => value && !SAFE_ID.test(value)
    ) ||
    (entry.experience &&
      entry.experience.split("+").some(value => !value || !SAFE_ID.test(value)))
  ) {
    throw new Error("地图和选项标识只能包含字母、数字、点、下划线或横线");
  }
  const attributes = [`Map="${entry.map}"`];
  if (entry.experience) {
    const name = entry.experience.includes("+") ? "Experiences" : "Experience";
    attributes.push(`${name}="${entry.experience}"`);
  }
  if (entry.lighting) attributes.push(`Lighting="${entry.lighting}"`);
  if (entry.zoneAlternator)
    attributes.push(`ZoneAlternator="${entry.zoneAlternator}"`);
  const line = `.RotationEntries=(${attributes.join(",")})`;
  return edit(text, (lines, start, end) => {
    const indexes = entryIndexes(lines, start, end);
    const clear = lines.findIndex(
      (value, index) =>
        index > start && index < end && /^\s*!RotationEntries\s*=/.test(value)
    );
    const insertAt = indexes.length
      ? indexes[indexes.length - 1] + 1
      : clear >= 0
        ? clear + 1
        : end;
    lines.splice(insertAt, 0, line);
  });
}

/** Locate top-level attributes without rewriting unrelated fields or quoted commas. */
function experienceAttribute(line: string) {
  const opening = line.indexOf("(");
  if (opening < 0)
    throw new Error("存在无法识别的地图条目，请在配置文件页编辑");
  let quoted = false;
  let depth = 1;
  let start = opening + 1;
  const fields: { start: number; end: number; value: string }[] = [];
  for (let index = start; index < line.length; index++) {
    const char = line[index];
    if (quoted && char === "\\") {
      index++;
      continue;
    }
    if (char === '"') quoted = !quoted;
    if (quoted) continue;
    if (char === "(") depth++;
    if (char === ")") depth--;
    if ((char === "," && depth === 1) || depth === 0) {
      fields.push({ start, end: index, value: line.slice(start, index) });
      start = index + 1;
    }
    if (depth === 0) {
      const matches = fields.filter(field =>
        /^\s*Experiences?\s*=/i.test(field.value)
      );
      if (matches.length > 1)
        throw new Error("轮换条目包含重复的体验字段，请先检查原文。");
      const attribute = matches[0];
      if (!attribute) return null;
      const match =
        /^(\s*)Experiences?(\s*=\s*)(?:"([^"\r\n]*)"|([^"\r\n]*?))(\s*)$/i.exec(
          attribute.value
        );
      if (!match) throw new Error("轮换体验字段无法安全修改，请先检查原文。");
      return {
        ...attribute,
        leading: match[1],
        assignment: match[2],
        trailing: match[5],
        values: (match[3] ?? match[4] ?? "")
          .split("+")
          .map(value => value.trim())
          .filter(Boolean)
      };
    }
  }
  throw new Error("轮换体验字段无法安全修改，请先检查原文。");
}

/** Only modifies experience attributes in a real rotation document; adds no defaults. */
export function setInfantryOnRotation(
  text: string,
  enabled: boolean,
  offered: (map: string) => boolean
) {
  let changed = 0;
  let unchanged = 0;
  const skippedMaps: string[] = [];
  const result = edit(text, (lines, start, end) => {
    if (
      lines.filter(line => line.trim().toLowerCase() === SECTION.toLowerCase())
        .length !== 1
    )
      throw new Error("存在重复的轮换段落，请先检查原文。");
    for (const index of entryIndexes(lines, start, end)) {
      const line = lines[index];
      const map = field(line, "Map");
      if (!map) throw new Error("存在无法识别的地图条目，请在配置文件页编辑");
      const attribute = experienceAttribute(line);
      const values = attribute?.values ?? [];
      const has = hasInfantryOnly(values);
      if (has === enabled) {
        unchanged++;
        continue;
      }
      const baseMode = values.some(value => !/^KOTH_/i.test(value));
      if (!attribute || !baseMode || (enabled && !offered(map))) {
        skippedMaps.push(map);
        continue;
      }
      const next = enabled
        ? [...values, INFANTRY_ONLY]
        : values.filter(value => !hasInfantryOnly([value]));
      const name = next.length > 1 ? "Experiences" : "Experience";
      const replacement = `${attribute.leading}${name}${attribute.assignment}"${next.join("+")}"${attribute.trailing}`;
      lines[index] =
        line.slice(0, attribute.start) +
        replacement +
        line.slice(attribute.end);
      changed++;
    }
  });
  return {
    text: result,
    changed,
    unchanged,
    skipped: skippedMaps.length,
    skippedMaps: [...new Set(skippedMaps)]
  };
}
