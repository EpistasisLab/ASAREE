import { useMemo, useState } from 'react'
import { useQueries, useQuery } from '@tanstack/react-query'
import {
  Archive,
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  FlaskConical,
  Layers,
  SlidersHorizontal,
} from 'lucide-react'
import { useNavigate } from 'react-router-dom'
import { AppHeader } from '@/components/AppHeader'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { Switch } from '@/components/ui/switch'
import { formatDate, formatRelative } from '@/lib/format'
import { factorCount, replicatesStatusAccent } from '@/lib/experiment'
import { cardAccent } from '@/lib/utils'
import { experimentsApi } from '@/api/client'
import type { Experiment } from '@/types/experiments'

type SortKey = 'name' | 'design_type' | 'created_at'
type SortDir = 'asc' | 'desc'

const SORT_LABELS: Record<SortKey, string> = {
  created_at: 'Created',
  name: 'Name',
  design_type: 'Design',
}

function sortValue(experiment: Experiment, key: SortKey): string | number {
  if (key === 'created_at') return new Date(experiment.created_at).getTime()
  return experiment[key].toLowerCase()
}

const PAGE_SIZE = 9

export function ExperimentsPage() {
  const navigate = useNavigate()
  const [includeArchived, setIncludeArchived] = useState(false)
  const { data, isLoading } = useQuery({
    queryKey: ['experiments', { includeArchived }],
    queryFn: () => experimentsApi.list({ includeArchived }),
  })
  const [sort, setSort] = useState<{ key: SortKey; dir: SortDir }>({ key: 'created_at', dir: 'desc' })
  const [page, setPage] = useState(1)

  const sorted = useMemo(() => {
    if (!data) return data
    const rows = [...data]
    rows.sort((a, b) => {
      const av = sortValue(a, sort.key)
      const bv = sortValue(b, sort.key)
      const cmp = av < bv ? -1 : av > bv ? 1 : 0
      return sort.dir === 'asc' ? cmp : -cmp
    })
    return rows
  }, [data, sort])

  const totalPages = Math.max(1, Math.ceil((sorted?.length ?? 0) / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const paged = sorted?.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  // One replicate-list fetch per visible tile, to tint each by completion status --
  // the same N+1 tradeoff already accepted for the Agents section on the
  // detail page. Fine at a page size of 9; revisit with a real aggregate
  // endpoint if this page ever needs to show many more at once.
  const replicateQueries = useQueries({
    queries: (paged ?? []).map((experiment) => ({
      queryKey: ['experiments', experiment.id, 'replicates'],
      queryFn: () => experimentsApi.listReplicates(experiment.id),
    })),
  })
  const accentByExperiment = new Map(
    (paged ?? []).map((experiment, i) => [experiment.id, replicatesStatusAccent(replicateQueries[i]?.data)]),
  )

  return (
    <div className="min-h-svh bg-muted/30">
      <AppHeader />

      <main className="mx-auto max-w-6xl space-y-6 px-6 py-10">
        <div className="flex items-center gap-2.5">
          <FlaskConical className="size-6 text-primary" />
          <div>
            <h1 className="text-2xl font-semibold tracking-tight">Experiments</h1>
            <p className="text-sm text-muted-foreground">Factorial sweeps run against this workspace.</p>
          </div>
        </div>

        <div className="flex items-center justify-between">
          <p className="text-sm text-muted-foreground">
            {isLoading ? 'Loading…' : `${sorted?.length ?? 0} experiment${sorted?.length === 1 ? '' : 's'}`}
          </p>
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground">Show archived</span>
            {/* Not gated behind "sorted.length > 0" like the Sort controls below --
                otherwise archiving your only experiment hides this toggle along with
                the (now empty) list, with no way to reveal it again. */}
            <Switch checked={includeArchived} onCheckedChange={setIncludeArchived} aria-label="Show archived" />
            {sorted && sorted.length > 0 && (
              <>
                <span className="text-xs text-muted-foreground">Sort</span>
                <Select
                  value={sort.key}
                  onValueChange={(value) => {
                    if (value === null) return
                    setSort((prev) => ({ ...prev, key: value as SortKey }))
                    setPage(1)
                  }}
                >
                  <SelectTrigger size="sm" className="w-32">
                    <SelectValue>{(value: SortKey) => SORT_LABELS[value]}</SelectValue>
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="created_at">Created</SelectItem>
                    <SelectItem value="name">Name</SelectItem>
                    <SelectItem value="design_type">Design</SelectItem>
                  </SelectContent>
                </Select>
                <Button
                  variant="outline"
                  size="icon-sm"
                  onClick={() => {
                    setSort((prev) => ({ ...prev, dir: prev.dir === 'asc' ? 'desc' : 'asc' }))
                    setPage(1)
                  }}
                  aria-label={sort.dir === 'asc' ? 'Sort ascending' : 'Sort descending'}
                >
                  {sort.dir === 'asc' ? <ArrowUp className="size-4" /> : <ArrowDown className="size-4" />}
                </Button>
              </>
            )}
          </div>
        </div>

        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-36 w-full" />
            ))}
          </div>
        ) : !data || data.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-xl border border-dashed py-16 text-center">
            <FlaskConical className="size-8 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">No experiments yet.</p>
          </div>
        ) : (
          <>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {(paged ?? []).map((experiment, i) => {
                const factors = factorCount(experiment.design_spec)
                const accent = accentByExperiment.get(experiment.id) ?? 'var(--primary)'
                return (
                  <Card
                    key={experiment.id}
                    style={{ animationDelay: `${i * 40}ms`, ...cardAccent(accent) }}
                    className="animate-in fade-in slide-in-from-bottom-2 fill-mode-backwards cursor-pointer duration-300 transition-[transform,box-shadow] hover:scale-[1.02] hover:shadow-[0_0_32px_-8px_var(--card-accent,var(--primary))] active:scale-[0.99]"
                    onClick={() => navigate(`/experiments/${experiment.id}/protocol`)}
                  >
                    {/* Retrofuturist top accent strip, tinted by completion status */}
                    <div className="pointer-events-none absolute inset-x-0 top-0 h-0.5 bg-gradient-to-r from-[color:var(--card-accent,var(--primary))]/0 via-[color:var(--card-accent,var(--primary))] to-[color:var(--card-accent,var(--primary))]/0" />
                    {/* Faint watermark, purely decorative */}
                    <FlaskConical className="pointer-events-none absolute -right-3 -bottom-3 size-24 text-[color:var(--card-accent,var(--primary))]/[0.04]" />
                    {/* Hover shine sweep */}
                    <div className="pointer-events-none absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/[0.04] to-transparent transition-transform duration-700 ease-out group-hover/card:translate-x-full" />

                    <CardHeader>
                      <div className="flex min-w-0 items-start justify-between gap-2">
                        <CardTitle className="min-w-0 flex-1 truncate">{experiment.name}</CardTitle>
                        <div className="flex shrink-0 flex-col items-end gap-1">
                          {experiment.archived_at !== null && (
                            <Badge variant="outline" className="gap-1 font-normal text-muted-foreground">
                              <Archive className="size-3" />
                              Archived
                            </Badge>
                          )}
                          <Badge variant="outline" className="gap-1 font-normal text-muted-foreground">
                            <Layers className="size-3" />
                            {experiment.design_type}
                          </Badge>
                          {factors !== null && (
                            <Badge variant="outline" className="gap-1 font-normal text-muted-foreground">
                              <SlidersHorizontal className="size-3" />
                              {factors} factor{factors === 1 ? '' : 's'}
                            </Badge>
                          )}
                        </div>
                      </div>
                      <CardDescription
                        className="line-clamp-3 min-h-15"
                        title={experiment.description ?? undefined}
                      >
                        {experiment.description ?? 'No description'}
                      </CardDescription>
                    </CardHeader>
                    <CardContent className="flex items-center justify-between text-xs text-muted-foreground">
                      <div className="flex items-center gap-2 font-mono">
                        <span title={formatDate(experiment.created_at)}>{formatRelative(experiment.created_at)}</span>
                        <span className="text-muted-foreground/40">·</span>
                        <span className="text-muted-foreground/60">#{experiment.id.slice(0, 8)}</span>
                      </div>
                      <ArrowRight className="size-4 opacity-60 transition-all group-hover/card:translate-x-1 group-hover/card:text-[color:var(--card-accent,var(--primary))] group-hover/card:opacity-100" />
                    </CardContent>
                  </Card>
                )
              })}
            </div>

            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                Showing {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, sorted!.length)} of{' '}
                {sorted!.length}
              </p>
              <div className="flex items-center gap-2">
                <Button
                  variant="outline"
                  size="icon-sm"
                  disabled={currentPage <= 1}
                  onClick={() => setPage((p) => p - 1)}
                  aria-label="Previous page"
                >
                  <ChevronLeft className="size-4" />
                </Button>
                <span className="text-xs text-muted-foreground">
                  Page {currentPage} of {totalPages}
                </span>
                <Button
                  variant="outline"
                  size="icon-sm"
                  disabled={currentPage >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                  aria-label="Next page"
                >
                  <ChevronRight className="size-4" />
                </Button>
              </div>
            </div>
          </>
        )}
      </main>
    </div>
  )
}
