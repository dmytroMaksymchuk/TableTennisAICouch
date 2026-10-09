export function fmtSeconds(s: number): string {
  return `${s.toFixed(2)}s`;
}

export function fmtMetric(value: number | null, unit: string): string {
  if (value === null || value === undefined) return "n/a";
  return `${Number(value.toFixed(2))} ${unit}`;
}

export function labelOf(name: string): string {
  return name.replace(/_/g, " ");
}
