import type { NodeRunStatus, ProtocolRun } from '@/types/protocols'

// Shared by ProtocolCanvas's own single-run polling and the "run all cells"
// batch polling on ProtocolCanvasPage -- one definition of "done" for both.
export const TERMINAL_RUN_STATUSES = new Set<ProtocolRun['status']>([
  'completed',
  'failed',
  'cancelled',
  // A conversation that ran out of budget is finished, not stuck: nothing
  // further will be scheduled for it, so polling must stop here too.
  'limit_reached',
])

// Same status-color language the app already uses for cell-scoring progress
// (replicatesStatusAccent in lib/experiment.ts): amber = in progress/queued, cyan
// = actively running, emerald = done, dim = not started/skipped, red =
// failed. A different status domain, but the same color meanings everywhere
// reads as one consistent system rather than two competing ones.
//
// *truncated* is the one thing that overrides the status it is paired with: a
// run cut off by its iteration ceiling is recorded as `completed` on purpose
// (its work is real and downstream nodes consumed it), but a green "Done" is
// the wrong thing to tell someone whose agent never got to write its answer.
// Amber, the same "finished, with a caveat" color the app already uses.
export function nodeRunBadge(
  status: NodeRunStatus | undefined,
  truncated = false,
): { label: string; className: string } | null {
  if (status === 'completed' && truncated) {
    return {
      label: 'Hit iteration limit',
      className: 'border-transparent bg-[color-mix(in_oklch,var(--chart-4),transparent_80%)] text-[color:var(--chart-4)]',
    }
  }
  switch (status) {
    case 'pending':
      return { label: 'Queued', className: 'border-transparent bg-[color-mix(in_oklch,var(--chart-4),transparent_80%)] text-[color:var(--chart-4)]' }
    case 'running':
      return { label: 'Running', className: 'animate-pulse border-transparent bg-[color-mix(in_oklch,var(--primary),transparent_80%)] text-[color:var(--primary)]' }
    case 'completed':
      return { label: 'Done', className: 'border-transparent bg-[color-mix(in_oklch,var(--chart-3),transparent_80%)] text-[color:var(--chart-3)]' }
    case 'failed':
      return { label: 'Failed', className: 'border-transparent bg-destructive/10 text-destructive' }
    case 'skipped':
      return { label: 'Skipped', className: 'border-transparent bg-muted text-muted-foreground' }
    case 'cancelled':
      return { label: 'Cancelled', className: 'border-transparent bg-muted text-muted-foreground' }
    default:
      return null
  }
}
