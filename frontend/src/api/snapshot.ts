import { t, locale } from "@/i18n";
export interface SnapshotMeta {
  observedAt: string;
  stale: boolean;
}

export function formatObservedAt(value: string | null): string {
  if (!value) return t("尚未采集");
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? t("时间未知")
    : date.toLocaleString(locale.value);
}
