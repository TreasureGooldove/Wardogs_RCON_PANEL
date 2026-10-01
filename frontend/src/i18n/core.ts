export type Messages = Record<string, string>;
export type NamedValues = Record<string, unknown>;

export function translate(
  source: unknown,
  messages: Messages,
  values: NamedValues = {}
): string {
  if (source === null || source === undefined) return "";
  const key = String(source);
  const message = Object.hasOwn(messages, key) ? messages[key] : key;
  // Replace once: player names and other inserted values are never translated
  // or recursively interpreted as placeholders. Vue escapes rendered text.
  return message.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g, (token, name) =>
    Object.hasOwn(values, name) ? String(values[name] ?? "") : token
  );
}

export const LOCALE_CODES = [
  "zh-CN",
  "zh-TW",
  "en-US",
  "ja-JP",
  "ko-KR"
] as const;
export type LocaleCode = (typeof LOCALE_CODES)[number];

export function normalizeLocale(value: unknown): LocaleCode {
  return LOCALE_CODES.includes(value as LocaleCode)
    ? (value as LocaleCode)
    : "zh-CN";
}
