import { computed, ref, type App } from "vue";
import zh from "./zh-CN.json";
import en from "./en-US.json";
import ja from "./ja-JP.json";
import ko from "./ko-KR.json";
import zhElement from "element-plus/es/locale/lang/zh-cn";
import enElement from "element-plus/es/locale/lang/en";
import jaElement from "element-plus/es/locale/lang/ja";
import koElement from "element-plus/es/locale/lang/ko";
import {
  normalizeLocale,
  translate,
  type Messages,
  type NamedValues,
  type LocaleCode
} from "./core";

const storageKey = "wardogs-panel-locale";
function savedLocale(): LocaleCode {
  try {
    return normalizeLocale(localStorage.getItem(storageKey));
  } catch {
    return "zh-CN";
  }
}
export const locale = ref<LocaleCode>(savedLocale());
const messages: Record<LocaleCode, Messages> = {
  "zh-CN": zh,
  "en-US": en,
  "ja-JP": ja,
  "ko-KR": ko
};
export const languages = [
  { code: "zh-CN", label: "简体中文", aiTranslated: false },
  { code: "en-US", label: "English · AI translated", aiTranslated: true },
  { code: "ja-JP", label: "日本語 · AI 翻訳", aiTranslated: true },
  { code: "ko-KR", label: "한국어 · AI 번역", aiTranslated: true }
] as const;
export const activeLanguage = computed(
  () => languages.find(item => item.code === locale.value)!
);
export const elementLocale = computed(
  () =>
    ({
      "zh-CN": zhElement,
      "en-US": enElement,
      "ja-JP": jaElement,
      "ko-KR": koElement
    })[locale.value]
);

export function t(source: unknown, values?: NamedValues): string {
  return translate(source, messages[locale.value], values);
}

export function setLocale(value: unknown): void {
  locale.value = normalizeLocale(value);
  try {
    localStorage.setItem(storageKey, locale.value);
  } catch {
    /* Storage is optional. */
  }
}

export function formatDate(value: string | Date | null | undefined): string {
  if (!value) return t("尚未采集");
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime())
    ? t("时间未知")
    : date.toLocaleString(locale.value);
}

export const panelI18n = {
  install(app: App) {
    app.config.globalProperties.$t = t;
  }
};

declare module "vue" {
  interface ComponentCustomProperties {
    $t: typeof t;
  }
}
