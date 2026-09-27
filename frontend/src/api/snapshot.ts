export interface SnapshotMeta {
  observedAt: string;
  stale: boolean;
}

export function formatObservedAt(value: string | null): string {
  if (!value) return "尚未采集";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "时间未知" : date.toLocaleString("zh-CN");
}
