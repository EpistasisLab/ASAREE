/** Shared status language for execution summaries, filters, and replicate badges. */
export const executionStatusLabels: Record<string, string> = {
  not_started: 'Not started', pending: 'Queued', running: 'Running', finalizing: 'Finalizing',
  completed: 'Completed', failed: 'Failed', cancelled: 'Cancelled', limit_reached: 'Hit iteration limit',
  scoring_failed: 'Scoring failed',
}

export const executionStatusColors: Record<string, string> = {
  not_started: 'text-muted-foreground',
  pending: 'text-[color:var(--chart-4)]',
  running: 'text-primary',
  finalizing: 'text-[color:var(--chart-2)]',
  completed: 'text-[color:var(--chart-3)]',
  failed: 'text-destructive',
  cancelled: 'text-[color:var(--status-cancelled)]',
  limit_reached: 'text-[color:var(--status-limit)]',
  scoring_failed: 'text-[color:var(--status-scoring-failed)]',
}

export function executionStatusColor(status: string): string {
  return executionStatusColors[status === 'queued' ? 'pending' : status] ?? 'text-muted-foreground'
}

export function executionStatusCount(counts: Record<string, number> | undefined, status: string): number {
  return (counts?.[status] ?? 0) + (status === 'running' ? counts?.finalizing ?? 0 : 0)
}

export function executionStatusPillColor(status: string, filter = ''): string {
  const matches = !filter || status === filter || (filter === 'running' && status === 'finalizing')
  return matches ? `opacity-70 ${executionStatusColor(status)}` : 'opacity-50 text-muted-foreground'
}
