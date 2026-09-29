const formatters = new Map<number, Intl.NumberFormat>();

export function formatNumber(value: number | null | undefined, digits: number): string {
  if (value == null) return '—';
  let formatter = formatters.get(digits);
  if (!formatter) {
    formatter = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: digits });
    formatters.set(digits, formatter);
  }
  return formatter.format(value);
}
