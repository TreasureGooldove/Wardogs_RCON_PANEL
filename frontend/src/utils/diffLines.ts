export interface DiffLine {
  kind: "added" | "removed";
  text: string;
}

export function changedLines(before: string, after: string): DiffLine[] {
  const oldLines = before.split(/\r?\n/);
  const newLines = after.split(/\r?\n/);
  const oldCount = oldLines.length;
  const newCount = newLines.length;
  if (oldCount * newCount > 200_000) return [];
  const lcs: number[][] = Array.from({ length: oldCount + 1 }, () =>
    Array<number>(newCount + 1).fill(0)
  );
  for (let i = oldCount - 1; i >= 0; i -= 1) {
    for (let j = newCount - 1; j >= 0; j -= 1) {
      lcs[i][j] = oldLines[i] === newLines[j]
        ? 1 + lcs[i + 1][j + 1]
        : Math.max(lcs[i + 1][j], lcs[i][j + 1]);
    }
  }
  const result: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while ((i < oldCount || j < newCount) && result.length < 500) {
    if (i < oldCount && j < newCount && oldLines[i] === newLines[j]) {
      i += 1;
      j += 1;
    } else if (j < newCount && (i === oldCount || lcs[i][j + 1] >= lcs[i + 1][j])) {
      result.push({ kind: "added", text: newLines[j] });
      j += 1;
    } else {
      result.push({ kind: "removed", text: oldLines[i] });
      i += 1;
    }
  }
  return result;
}
