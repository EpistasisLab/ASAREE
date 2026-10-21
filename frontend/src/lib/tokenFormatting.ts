const compactTokens = new Intl.NumberFormat('en-US', {
  notation: 'compact', maximumFractionDigits: 2,
})

export function formatTokens(value: number | null | undefined): string {
  return value == null || !Number.isFinite(value) ? '—' : compactTokens.format(value)
}
