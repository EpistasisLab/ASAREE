const compactTokens = new Intl.NumberFormat('en-US', {
  notation: 'compact', maximumFractionDigits: 2,
})
const compactLargeTokens = new Intl.NumberFormat('en-US', {
  notation: 'compact', minimumFractionDigits: 2, maximumFractionDigits: 2,
})

export function formatTokens(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return '—'
  return (Math.abs(value) >= 1_000_000 ? compactLargeTokens : compactTokens).format(value)
}
