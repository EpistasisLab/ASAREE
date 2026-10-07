import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import type { ResultNodeRun } from '@/types/experiments'
import { ReceivedPromptPanel, RunStepTrace, UnresolvedReferencesNote } from './NodeRunOutputPanel'

function formatCurrency(value: number | null): string {
  return value === null || !Number.isFinite(value) ? 'Not reported' : new Intl.NumberFormat(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value)
}

function formatNumber(value: number | null): string {
  return value === null || !Number.isFinite(value) ? 'Not reported' : new Intl.NumberFormat(undefined, { maximumFractionDigits: 0 }).format(value)
}

function nodeStatusClass(status: string): string {
  if (status === 'completed') return 'border-transparent bg-[color:var(--chart-3)]/10 text-[color:var(--chart-3)]'
  if (status === 'failed' || status === 'cancelled') return 'border-transparent bg-destructive/10 text-destructive'
  if (status === 'running' || status === 'queued') return 'border-transparent bg-primary/10 text-primary'
  return 'border-transparent bg-muted text-muted-foreground'
}

export function ResultTimelineNode({ node, defaultOpen = false }: { node: ResultNodeRun; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  const hasDetails = Boolean(node.error || node.output_text || node.agent_run_id)

  return (
    <li className="overflow-hidden rounded-md border bg-card">
      <button
        type="button"
        onClick={() => hasDetails && setOpen((value) => !value)}
        disabled={!hasDetails}
        aria-expanded={hasDetails ? open : undefined}
        className="flex w-full items-center gap-2 px-3 py-2 text-left transition-colors hover:bg-muted/40 disabled:cursor-default disabled:hover:bg-transparent"
      >
        {hasDetails ? (
          open ? <ChevronDown className="size-4 shrink-0 text-muted-foreground" /> : <ChevronRight className="size-4 shrink-0 text-muted-foreground" />
        ) : <span className="size-4 shrink-0" />}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium" title={node.node_id}>{node.node_label}</span>
          <span className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-muted-foreground">
            {node.cost_usd !== null && <span>{formatCurrency(node.cost_usd)}</span>}
            {node.total_tokens !== null && <span>{formatNumber(node.total_tokens)} tokens</span>}
            {!hasDetails && <span>No output details recorded</span>}
          </span>
        </span>
        <Badge variant="outline" className={`shrink-0 capitalize ${nodeStatusClass(node.status)}`}>{node.status}</Badge>
      </button>
      {open && (
        <div className="space-y-4 border-t bg-muted/15 px-3 py-3">
          {node.error && <section className="space-y-1.5"><h4 className="text-xs font-medium">Error</h4><p className="rounded border border-destructive/30 bg-destructive/5 p-2 text-xs whitespace-pre-wrap break-words text-destructive">{node.error}</p></section>}
          {/* Received before produced, so one node reads as the handoff it
              was: what it was given, then what it made of it. */}
          {node.agent_run_id && <ReceivedPromptPanel runId={node.agent_run_id} />}
          <UnresolvedReferencesNote names={node.unresolved_reference_labels ?? []} />
          {node.output_text ? (
            <section className="space-y-1.5">
              <h4 className="text-xs font-medium">Output</h4>
              <p className="max-h-64 overflow-y-auto rounded border bg-background/70 p-2 font-mono text-xs whitespace-pre-wrap break-words">{node.output_text}</p>
            </section>
          ) : !node.error && <p className="text-xs text-muted-foreground">No output was recorded for this node.</p>}
          {node.agent_run_id && <RunStepTrace runId={node.agent_run_id} />}
        </div>
      )}
    </li>
  )
}

