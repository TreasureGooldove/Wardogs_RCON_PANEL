export function validateActionMessage(value: string): string {
  const trimmed = value.trim();
  if (!trimmed || trimmed.length > 200) return "消息需为 1–200 个字符";
  if (/[\u0000-\u001f\u007f-\u009f\u2028\u2029]/u.test(value)) {
    return "消息只能输入单行文字，不能含换行或控制字符";
  }
  return "";
}

export function validateMapId(value: string): string {
  return value === value.trim() &&
    value !== "." &&
    value !== ".." &&
    /^[A-Za-z0-9][A-Za-z0-9_ .-]{0,95}$/u.test(value)
    ? ""
    : "地图 ID 格式无效，请从地图目录选择";
}
