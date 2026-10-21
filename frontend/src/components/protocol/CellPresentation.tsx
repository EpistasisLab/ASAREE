import type { ReactNode } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { ChevronDown, ExternalLink } from 'lucide-react'
import { executionStatusPillColor, executionStatusLabels, STATUS_PILL_SIZE } from '@/lib/executionStatus'

/** Both execution modes use this layout; callers supply their progress and actions. */
export function CellCard({ conditions, cellNumber, replicateCount, expanded, onToggle, progress, badges, actions, metric, children, results = false, listId, toggleLabel }: {
  label: string; conditions: string[]; replicateCount: number; expanded: boolean; onToggle: () => void
  progress?: ReactNode; badges?: ReactNode; actions?: ReactNode; metric?: ReactNode; children: ReactNode
  results?: boolean; listId: string; toggleLabel?: string; cellNumber?: number
}) {
  const summary = (results ? conditions : conditions.slice(0, 2)).join(' · ') || (results ? 'No varying factors' : 'Cell')
  return <CellFrame results={results} expanded={expanded}>
    <div className="relative flex items-start gap-2 px-3 py-2">
      <button type="button" onClick={onToggle} aria-label={toggleLabel} aria-expanded={results ? undefined : expanded} aria-controls={results ? undefined : listId}
        className={`flex min-w-0 flex-1 cursor-pointer gap-3 text-left transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${results ? 'items-center' : 'items-start'}`}>
        {!results && <ChevronDown className={`mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform ${expanded ? '' : '-rotate-90'}`} />}
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="min-w-0 flex-1 truncate text-sm font-medium" title={cellNumber != null ? `Cell ${cellNumber}: ${summary}` : summary}>
              {cellNumber != null && <span className="font-mono text-muted-foreground">Cell {cellNumber}: </span>}{summary}
            </p>
            {!results && badges}
          </div>
          {results && badges && <div className="mt-1 flex flex-wrap gap-1">{badges}</div>}
          {!results && conditions.length > 2 && <div className="mt-1.5 flex flex-wrap gap-1">{conditions.slice(2).map((condition, index) => <Badge key={index} variant="outline" className="max-w-full font-mono text-[0.65rem] font-normal"><span className="truncate">{condition}</span></Badge>)}</div>}
          {progress && <p className="mt-1 flex flex-wrap gap-x-2 text-xs text-muted-foreground">{progress}</p>}
        </div>
        {metric}
        {results && <ExternalLink aria-hidden="true" className="absolute right-1.5 top-1.5 size-3 text-muted-foreground" />}
      </button>
      <div className="flex shrink-0 self-end items-center gap-1.5">
        <Badge variant="outline" className={`${STATUS_PILL_SIZE} border-[color:var(--chart-2)] text-[color:var(--chart-2)] opacity-70`} title="Total generated replicates for this condition">{replicateCount} {replicateCount === 1 ? 'replicate' : 'replicates'}</Badge>
        {actions && <div className="flex items-center gap-1.5">{actions}</div>}
      </div>
    </div>
    {!results && expanded && <div id={listId} className="border-t bg-muted/20 px-3 py-2.5">{children}</div>}
  </CellFrame>
}

export function ReplicateRow({ number, detail, badges, status, actions, onView, viewLabel = 'View result', viewAriaLabel, results = false, selected = false, error }: {
  number: number; detail?: ReactNode; badges?: ReactNode; status?: ReactNode; actions?: ReactNode
  onView?: () => void; viewLabel?: string; viewAriaLabel?: string; results?: boolean; error?: string | null
  selected?: boolean
}) {
  const content = <><div className="min-w-0 flex-1"><div className="flex items-center gap-1.5"><p className="text-sm font-medium">Replicate {number}</p>{badges}</div>{detail && <p className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground">{detail}</p>}</div>
    <div className="flex shrink-0 items-center gap-2">{status}{results ? onView && <ExternalLink className="size-3.5 text-muted-foreground" /> : <>{onView && <Button variant="outline" size="xs" className="h-5 px-1.5 text-[0.65rem]" aria-label={viewAriaLabel} onClick={onView}>{viewLabel}</Button>}{actions}</>}</div></>
  if (results) return <li><button type="button" disabled={!onView} aria-label={viewAriaLabel} aria-current={selected ? 'true' : undefined} onClick={onView} className={`flex w-full items-center gap-2 rounded-md border px-2.5 py-2 text-left transition-colors enabled:hover:border-primary/30 enabled:hover:bg-primary/5 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${selected ? 'border-primary/50 bg-primary/10' : 'bg-background'}`}>{content}</button></li>
  return <ReplicateFrame><div className="flex items-center justify-between gap-3">{content}</div>{error && <p className="mt-2 rounded bg-destructive/10 px-2 py-1.5 text-xs text-destructive">{error}</p>}</ReplicateFrame>
}

export function CellFrame({ children, results = false, expanded = false }: { children: ReactNode; results?: boolean; expanded?: boolean }) {
  return <div className={results ? `overflow-hidden rounded-lg border bg-card transition-shadow ${expanded ? 'shadow-[0_0_18px_-14px_var(--primary)]' : 'hover:border-muted-foreground/30'}` : 'overflow-hidden rounded-md border'}>{children}</div>
}

export function ReplicateFrame({ children }: { children: ReactNode }) {
  return <li className="rounded-md border bg-background px-2.5 py-2">{children}</li>
}

export function ReplicateStatus({ status, filter = '' }: { status: string; filter?: string }) {
  const label = executionStatusLabels[status] ?? (status === 'queued' ? 'Queued' : status.replaceAll('_', ' ').replace(/^./, letter => letter.toUpperCase()))
  return <Badge variant="outline" className={`${STATUS_PILL_SIZE} border-current ${executionStatusPillColor(status, filter)}`}>{label}</Badge>
}

export function CellRunAction({ children, onClick, disabled, label }: { children: ReactNode; onClick: () => void; disabled?: boolean; label?: string }) {
  return <Button size="xs" className="bg-[color:var(--chart-3)] text-primary-foreground hover:bg-[color:var(--chart-3)]/80" disabled={disabled} onClick={onClick} aria-label={label}>{children}</Button>
}

export function ReplicateRunAction({ children, onClick, disabled, label, title }: { children: ReactNode; onClick: () => void; disabled?: boolean; label?: string; title?: string }) {
  return <Button size="xs" className="h-5 px-1.5 text-[0.65rem]" disabled={disabled} onClick={onClick} aria-label={label} title={title}>{children}</Button>
}
