import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Maximize2, Minimize2 } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { experimentsApi } from '@/api/client'
import type { EmmCell, ExperimentAnalysis, FactorialEffect } from '@/types/experiments'

function fmt(n: number | undefined, digits = 3): string {
  return typeof n === 'number' && Number.isFinite(n) ? n.toFixed(digits) : '—'
}

function StatCard({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border px-2 py-1.5 text-center font-mono text-xs">
      <p className="text-base">{value}</p>
      <p className="text-muted-foreground">{label}</p>
    </div>
  )
}

function EmmTable({ cells, best }: { cells: EmmCell[]; best: EmmCell | null }) {
  return (
    <div className="overflow-hidden rounded-md border">
      <table className="w-full text-xs">
        <thead className="bg-muted/50 uppercase text-muted-foreground">
          <tr>
            <th className="px-2 py-1.5 text-left">Condition</th>
            <th className="px-2 py-1.5 text-right">Mean</th>
            <th className="px-2 py-1.5 text-right">95% CI</th>
            <th className="px-2 py-1.5 text-right">n</th>
          </tr>
        </thead>
        <tbody>
          {cells.map((c, i) => (
            <tr key={c._condition_label} className={i % 2 === 1 ? 'bg-muted/20' : ''}>
              <td className="px-2 py-1.5 font-mono">
                {c._condition_label}
                {best?._condition_label === c._condition_label && (
                  <Badge className="ml-1.5 border-transparent bg-[color-mix(in_oklch,var(--chart-3),transparent_80%)] text-[color:var(--chart-3)]">
                    Best
                  </Badge>
                )}
              </td>
              <td className="px-2 py-1.5 text-right font-mono">{fmt(c.mean)}</td>
              <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">
                [{fmt(c.ci_lo)}, {fmt(c.ci_hi)}]
              </td>
              <td className="px-2 py-1.5 text-right font-mono text-muted-foreground">{c.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function EffectsTable({ effects, title }: { effects: FactorialEffect[]; title: string }) {
  if (effects.length === 0) return null
  return (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">{title}</p>
      <div className="overflow-hidden rounded-md border">
        <table className="w-full text-xs">
          <thead className="bg-muted/50 uppercase text-muted-foreground">
            <tr>
              <th className="px-2 py-1.5 text-left">Term</th>
              <th className="px-2 py-1.5 text-right">Estimate</th>
              <th className="px-2 py-1.5 text-right">p (FWER)</th>
            </tr>
          </thead>
          <tbody>
            {effects.map((e, i) => (
              <tr key={e.effect} className={i % 2 === 1 ? 'bg-muted/20' : ''}>
                <td className="px-2 py-1.5 font-mono">{e.effect}</td>
                <td className="px-2 py-1.5 text-right font-mono">{fmt(e.estimate_half_diff)}</td>
                <td className={`px-2 py-1.5 text-right font-mono ${e.p_maxstat_fwer < 0.05 ? 'text-[color:var(--chart-3)]' : 'text-muted-foreground'}`}>
                  {fmt(e.p_maxstat_fwer)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function ResultsContent({ analysis, bestCondition }: { analysis: ExperimentAnalysis; bestCondition: EmmCell | null }) {
  const mainEffects = analysis.factorial_effects.filter((e) => !e.effect.includes(':'))
  const interactions = analysis.factorial_effects.filter((e) => e.effect.includes(':'))

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-4 gap-2">
        <StatCard label="Scored" value={analysis.n_scored} />
        <StatCard label="Attempted" value={analysis.n_attempted} />
        <StatCard label="Failed" value={analysis.n_failed} />
        <StatCard label="Not yet run" value={analysis.n_not_yet_run} />
      </div>

      {bestCondition && (
        <div className="rounded-md border bg-[color-mix(in_oklch,var(--chart-3),transparent_92%)] px-3 py-2">
          <p className="text-xs font-medium text-muted-foreground">Best-performing configuration</p>
          <p className="font-mono text-sm">
            {bestCondition._condition_label} — {fmt(bestCondition.mean)} [{fmt(bestCondition.ci_lo)}, {fmt(bestCondition.ci_hi)}]
          </p>
        </div>
      )}

      <div className="space-y-1.5">
        <p className="text-xs font-medium text-muted-foreground">Condition comparisons (uncertainty via 95% CI)</p>
        <EmmTable cells={analysis.emm_cells} best={bestCondition} />
      </div>

      <EffectsTable effects={mainEffects} title="Factor effects" />
      <EffectsTable effects={interactions} title="Interactions" />

      {analysis.non_inferiority.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">Non-inferiority vs. reference</p>
          <div className="overflow-hidden rounded-md border">
            <table className="w-full text-xs">
              <thead className="bg-muted/50 uppercase text-muted-foreground">
                <tr>
                  <th className="px-2 py-1.5 text-left">Condition</th>
                  <th className="px-2 py-1.5 text-right">Contrast</th>
                  <th className="px-2 py-1.5 text-left">Decision</th>
                </tr>
              </thead>
              <tbody>
                {analysis.non_inferiority.map((row, i) => (
                  <tr key={row.condition} className={i % 2 === 1 ? 'bg-muted/20' : ''}>
                    <td className="px-2 py-1.5 font-mono">{row.condition}</td>
                    <td className="px-2 py-1.5 text-right font-mono">{fmt(row.contrast_vs_reference)}</td>
                    <td className="px-2 py-1.5 text-muted-foreground">{row.ni_decision ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}

// A "maximize" toggle for the whole Results content -- CLAUDE.md's
// established convention for a wide/dense view that can outgrow the side
// panel's own column (see cells/CellsTab.tsx for the other one): a fixed
// inset-0 overlay with an Escape handler and body-scroll lock, not the
// browser Fullscreen API. This is the "link into a larger/full-screen
// analysis view" the Results tab asks for.
export function ResultsTab({ experimentId }: { experimentId: string }) {
  const [fullscreen, setFullscreen] = useState(false)

  const resultsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'results'],
    queryFn: () => experimentsApi.getResults(experimentId),
  })

  useEffect(() => {
    if (!fullscreen) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = ''
    }
  }, [fullscreen])

  if (resultsQuery.isLoading) {
    return (
      <div className="space-y-3 p-3">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  if (resultsQuery.isError || !resultsQuery.data) {
    return (
      <div className="p-3">
        <p className="text-sm text-muted-foreground">Could not load this experiment's results.</p>
      </div>
    )
  }

  const { available, reason, analysis, best_condition } = resultsQuery.data

  if (!available || !analysis) {
    return (
      <div className="p-3">
        <p className="text-sm text-muted-foreground">{reason ?? 'No results available yet.'}</p>
      </div>
    )
  }

  if (fullscreen) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-background p-4">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm font-semibold">Results — full analysis</p>
          <Button variant="outline" size="icon-sm" aria-label="Exit fullscreen" onClick={() => setFullscreen(false)}>
            <Minimize2 className="size-4" />
          </Button>
        </div>
        <div className="flex-1 overflow-y-auto text-sm">
          <ResultsContent analysis={analysis} bestCondition={best_condition} />
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div className="flex justify-end">
        <Button variant="outline" size="sm" onClick={() => setFullscreen(true)}>
          <Maximize2 className="size-3.5" /> Maximize
        </Button>
      </div>
      <ResultsContent analysis={analysis} bestCondition={best_condition} />
    </div>
  )
}
