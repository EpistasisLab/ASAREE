import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { experimentsApi, protocolsApi } from '@/api/client'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { Trial } from '@/types/experiments'

const PAGE_SIZE = 20
const NON_FACTOR_KEYS = new Set(['replicate', 'seed', 'rep', 'trial', 'iteration'])
const STATUS_ORDER: Trial['status'][] = ['queued', 'running', 'completed', 'failed']

function statusBadge(status: Trial['status']) {
  return nodeRunBadge(status === 'queued' ? 'pending' : status)
}

function deriveFactorNames(trials: Trial[]): string[] {
  const names = new Set<string>()
  for (const t of trials) {
    for (const key of Object.keys(t.factor_values)) {
      if (!NON_FACTOR_KEYS.has(key)) names.add(key)
    }
  }
  return [...names].sort()
}

type SortKey = 'cell_label' | 'status' | 'updated_at'

function sortTrials(trials: Trial[], key: SortKey, direction: 'asc' | 'desc'): Trial[] {
  const sorted = [...trials].sort((a, b) => {
    const av = a[key]
    const bv = b[key]
    return av < bv ? -1 : av > bv ? 1 : 0
  })
  return direction === 'asc' ? sorted : sorted.reverse()
}

// The trial detail drill-in -- cell status/error plus, when the trial has a
// real ProtocolRun (run_id set), each pipeline node's own status/output/
// error (GET /protocols/{id}/runs/{runId}, already built for the canvas's
// own run-polling -- no new backend endpoint needed for this).
function TrialDetailDialog({
  trial,
  protocolId,
  onClose,
}: {
  trial: Trial | null
  protocolId: string
  onClose: () => void
}) {
  const runQuery = useQuery({
    queryKey: ['protocols', protocolId, 'runs', trial?.run_id],
    queryFn: () => protocolsApi.getRun(protocolId, trial!.run_id!),
    enabled: !!trial?.run_id,
  })

  return (
    <Dialog open={!!trial} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">{trial?.cell_label}</DialogTitle>
        </DialogHeader>
        {trial && (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(trial.factor_values).map(([k, v]) => (
                <Badge key={k} variant="outline" className="font-mono text-xs">
                  {k}={String(v)}
                </Badge>
              ))}
            </div>
            {trial.error && <p className="rounded-md bg-destructive/10 px-2.5 py-1.5 text-xs text-destructive">{trial.error}</p>}
            {!trial.run_id ? (
              <p className="text-xs text-muted-foreground">No pipeline run is linked to this trial yet.</p>
            ) : runQuery.isLoading ? (
              <Skeleton className="h-24 w-full" />
            ) : runQuery.isError || !runQuery.data ? (
              <p className="text-xs text-muted-foreground">Could not load this trial's run detail.</p>
            ) : (
              <div className="space-y-2">
                <p className="text-xs font-medium text-muted-foreground">Agent activity</p>
                {Object.entries(runQuery.data.node_runs).map(([nodeId, node]) => (
                  <div key={nodeId} className="rounded-md border px-2.5 py-1.5">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-mono text-xs">{nodeId}</span>
                      {(() => {
                        const badge = nodeRunBadge(node.status)
                        return badge ? <Badge className={badge.className}>{badge.label}</Badge> : null
                      })()}
                    </div>
                    {node.output_text && <p className="mt-1 truncate text-xs text-muted-foreground" title={node.output_text}>{node.output_text}</p>}
                    {node.error && <p className="mt-1 text-xs text-destructive">{node.error}</p>}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function RunsTab({ experimentId, protocolId }: { experimentId: string; protocolId: string }) {
  const [statusFilter, setStatusFilter] = useState<Trial['status'] | 'all'>('all')
  const [factorFilter, setFactorFilter] = useState<string>('__none__')
  const [factorValueFilter, setFactorValueFilter] = useState<string>('__all__')
  const [sortKey, setSortKey] = useState<SortKey>('updated_at')
  const [sortDirection, setSortDirection] = useState<'asc' | 'desc'>('desc')
  const [page, setPage] = useState(0)
  const [selectedTrial, setSelectedTrial] = useState<Trial | null>(null)

  const trialsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'runs'],
    queryFn: () => experimentsApi.listTrials(experimentId),
    refetchInterval: 3000,
  })

  const trials = trialsQuery.data ?? []
  const factorNames = useMemo(() => deriveFactorNames(trialsQuery.data ?? []), [trialsQuery.data])

  const factorValues = useMemo(() => {
    if (factorFilter === '__none__') return []
    return [...new Set((trialsQuery.data ?? []).map((t) => String(t.factor_values[factorFilter] ?? '')))].sort()
  }, [trialsQuery.data, factorFilter])

  const filtered = trials.filter((t) => {
    if (statusFilter !== 'all' && t.status !== statusFilter) return false
    if (factorFilter !== '__none__' && factorValueFilter !== '__all__' && String(t.factor_values[factorFilter] ?? '') !== factorValueFilter) return false
    return true
  })
  const sorted = sortTrials(filtered, sortKey, sortDirection)
  const totalPages = Math.max(1, Math.ceil(sorted.length / PAGE_SIZE))
  const paged = sorted.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)

  const counts = {
    total: trials.length,
    queued: trials.filter((t) => t.status === 'queued').length,
    running: trials.filter((t) => t.status === 'running').length,
    completed: trials.filter((t) => t.status === 'completed').length,
    failed: trials.filter((t) => t.status === 'failed').length,
  }
  const progressPct = counts.total > 0 ? Math.round(((counts.completed + counts.failed) / counts.total) * 100) : 0

  function onSort(key: SortKey) {
    if (key === sortKey) setSortDirection((d) => (d === 'asc' ? 'desc' : 'asc'))
    else {
      setSortKey(key)
      setSortDirection('asc')
    }
    setPage(0)
  }

  if (trialsQuery.isLoading) {
    return (
      <div className="space-y-3 p-3">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (trials.length === 0) {
    return (
      <div className="p-3">
        <p className="text-sm text-muted-foreground">
          No trials yet -- generate a design and run cells from the Design tab first.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div className="grid grid-cols-4 gap-2 text-center font-mono text-xs">
        <div className="rounded-md border px-2 py-1.5">
          <p className="text-base">{counts.queued}</p>
          <p className="text-muted-foreground">Queued</p>
        </div>
        <div className="rounded-md border px-2 py-1.5">
          <p className="text-base text-[color:var(--primary)]">{counts.running}</p>
          <p className="text-muted-foreground">Running</p>
        </div>
        <div className="rounded-md border px-2 py-1.5">
          <p className="text-base text-[color:var(--chart-3)]">{counts.completed}</p>
          <p className="text-muted-foreground">Completed</p>
        </div>
        <div className="rounded-md border px-2 py-1.5">
          <p className="text-base text-destructive">{counts.failed}</p>
          <p className="text-muted-foreground">Failed</p>
        </div>
      </div>

      <div className="space-y-1">
        <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div className="h-full bg-[color:var(--chart-3)] transition-all" style={{ width: `${progressPct}%` }} />
        </div>
        <p className="font-mono text-xs text-muted-foreground">
          {counts.completed + counts.failed}/{counts.total} trials done ({progressPct}%)
        </p>
      </div>

      <div className="flex gap-1.5">
        <Select
          value={statusFilter}
          onValueChange={(v) => {
            if (!v) return
            setStatusFilter(v as Trial['status'] | 'all')
            setPage(0)
          }}
        >
          <SelectTrigger className="flex-1">
            <SelectValue>{() => (statusFilter === 'all' ? 'All statuses' : statusFilter)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">All statuses</SelectItem>
            {STATUS_ORDER.map((s) => (
              <SelectItem key={s} value={s}>
                {s}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={factorFilter}
          onValueChange={(v) => {
            if (v) {
              setFactorFilter(v)
              setFactorValueFilter('__all__')
              setPage(0)
            }
          }}
        >
          <SelectTrigger className="flex-1">
            <SelectValue>{() => (factorFilter === '__none__' ? 'Filter by factor…' : factorFilter)}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="__none__">(no factor filter)</SelectItem>
            {factorNames.map((name) => (
              <SelectItem key={name} value={name}>
                {name}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {factorFilter !== '__none__' && (
          <Select
            value={factorValueFilter}
            onValueChange={(v) => {
              if (!v) return
              setFactorValueFilter(v)
              setPage(0)
            }}
          >
            <SelectTrigger className="flex-1">
              <SelectValue>{() => (factorValueFilter === '__all__' ? 'Any value' : factorValueFilter)}</SelectValue>
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__all__">Any value</SelectItem>
              {factorValues.map((v) => (
                <SelectItem key={v} value={v}>
                  {v}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      <div className="overflow-hidden rounded-md border">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 uppercase text-muted-foreground">
            <tr>
              <th className="cursor-pointer px-2 py-1.5 text-left" onClick={() => onSort('cell_label')}>
                Cell {sortKey === 'cell_label' && (sortDirection === 'asc' ? '↑' : '↓')}
              </th>
              <th className="cursor-pointer px-2 py-1.5 text-left" onClick={() => onSort('status')}>
                Status {sortKey === 'status' && (sortDirection === 'asc' ? '↑' : '↓')}
              </th>
              <th className="cursor-pointer px-2 py-1.5 text-left" onClick={() => onSort('updated_at')}>
                Updated {sortKey === 'updated_at' && (sortDirection === 'asc' ? '↑' : '↓')}
              </th>
            </tr>
          </thead>
          <tbody>
            {paged.map((t, i) => {
              const badge = statusBadge(t.status)
              return (
                <tr
                  key={t.cell_label}
                  className={`cursor-pointer hover:bg-muted/50 ${i % 2 === 1 ? 'bg-muted/20' : ''}`}
                  onClick={() => setSelectedTrial(t)}
                >
                  <td className="truncate px-2 py-1.5 font-mono" title={t.cell_label}>
                    {t.cell_label}
                  </td>
                  <td className="px-2 py-1.5">{badge && <Badge className={badge.className}>{badge.label}</Badge>}</td>
                  <td className="px-2 py-1.5 font-mono text-muted-foreground">{new Date(t.updated_at).toLocaleString()}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="flex items-center justify-between">
          <Button variant="outline" size="sm" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>
            Previous
          </Button>
          <span className="font-mono text-xs text-muted-foreground">
            Page {page + 1}/{totalPages}
          </span>
          <Button variant="outline" size="sm" disabled={page >= totalPages - 1} onClick={() => setPage((p) => p + 1)}>
            Next
          </Button>
        </div>
      )}

      <TrialDetailDialog trial={selectedTrial} protocolId={protocolId} onClose={() => setSelectedTrial(null)} />
    </div>
  )
}
