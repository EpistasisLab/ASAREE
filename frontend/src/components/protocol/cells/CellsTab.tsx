import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, Download, Maximize2, Minimize2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { experimentsApi } from '@/api/client'
import { groupReplicatesIntoCells } from '@/lib/experiment'
import { sanitizeFilename } from '@/lib/utils'
import type { Experiment, Replicate } from '@/types/experiments'
import { CellsHeatmap } from './CellsHeatmap'
import { CellsTable } from './CellsTable'
import { DesignHistory } from './DesignHistory'

// `@container` so the heatmap inside sizes itself against THIS box rather
// than the viewport -- the same component renders in a drag-resizable ~320-
// 1100px panel column and in a full-viewport overlay, and dragging the panel
// wider has to visibly pay off without a width prop threaded through here.
function CellsBody({ experiment, cells }: { experiment: Experiment; cells: Replicate[] }) {
  return (
    <div className="@container space-y-4">
      <CellsHeatmap experiment={experiment} cells={cells} />
      {/* The one horizontally-scrolling box in this view. The column set is
          dynamic (one per derived factor + up to 4 metrics), so even maximized
          it can outgrow the viewport -- and at the panel's narrower widths it
          always does. Scrolling the table sideways inside its own border is
          the honest answer; dragging the panel wider and the Maximize button
          are how you stop having to. */}
      <div className="overflow-x-auto">
        <CellsTable experiment={experiment} cells={cells} />
      </div>
    </div>
  )
}

/** The experiment's raw design points, in the canvas side panel: the
 * factor-combination heatmap over the precise per-cell table. This is the
 * "what did each configuration score" view -- distinct from the Results tab,
 * which is the statistical analysis (effects, CIs, non-inferiority) computed
 * ON these numbers.
 *
 * Cells, replicate results, factor_values, and metric_values are factorial-design concepts -- the
 * only experiment type ASAREE's backend actually implements today
 * (ab_experiments/discoveries/etc. are explicitly out of scope on the model
 * itself), but design_type is a plain string specifically so another type
 * COULD exist later. Gate on it rather than silently assuming every
 * experiment has cells, so a future non-factorial type gets an explicit
 * "not available" line instead of an empty, broken-looking grid.
 *
 * Maximize is CLAUDE.md's established convention for a dense view that
 * outgrows the panel's column (same as ResultsTab): a fixed inset-0 overlay
 * with an Escape handler and a body-scroll lock, never the browser Fullscreen
 * API. The table is mounted in exactly one of the two places at a time, so
 * there's never a second copy carrying its own divergent sort/page state.
 */
export function CellsTab({ experiment }: { experiment: Experiment }) {
  const [fullscreen, setFullscreen] = useState(false)
  // null = the current design. Set from DesignHistory to inspect a superseded
  // revision's cells; owned here so the heatmap, the table, the scored tally
  // and the CSV button can't disagree about which design they're showing.
  const [revisionId, setRevisionId] = useState<string | null>(null)

  // For the current design this is the same query key the canvas page itself
  // uses, so it shares one cache entry with the top bar's cells readout and
  // DesignTab's own invalidation after generating a design -- no second fetch,
  // no chance of the two disagreeing about how many cells exist. A superseded
  // revision gets its own key: it's a different set of rows, and it must not
  // overwrite the shared entry every other reader is looking at.
  const replicatesQuery = useQuery({
    queryKey: revisionId ? ['experiments', experiment.id, 'replicates', revisionId] : ['experiments', experiment.id, 'replicates'],
    queryFn: () => experimentsApi.listReplicates(experiment.id, revisionId ?? undefined),
  })

  useEffect(() => {
    if (!fullscreen) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setFullscreen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    const previousOverflow = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      document.body.style.overflow = previousOverflow
    }
  }, [fullscreen])

  async function handleDownloadCsv() {
    const blob = await experimentsApi.downloadReplicatesCsv(experiment.id)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${sanitizeFilename(experiment.name, 'experiment')}-replicates.csv`
    a.click()
    URL.revokeObjectURL(url)
  }

  if (experiment.design_type !== 'factorial') {
    return (
      <div className="p-3">
        <p className="text-sm text-muted-foreground">
          Cell-based results aren't available for &ldquo;{experiment.design_type}&rdquo; experiments.
        </p>
      </div>
    )
  }

  if (replicatesQuery.isLoading) {
    return (
      <div className="space-y-3 p-3">
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-32 w-full" />
      </div>
    )
  }

  const cells = replicatesQuery.data
  const cellCount = cells ? groupReplicatesIntoCells(cells).length : 0
  const scoredReplicates = cells?.filter((replicate) => replicate.metric_values).length ?? 0
  const viewingHistory = revisionId !== null

  // The history list stays mounted through the error/empty cases below: an
  // experiment whose current design generated nothing still has history worth
  // seeing (and getting back out of), and hiding it would strand a user who
  // selected a revision that has since been deleted.
  const history = (
    <DesignHistory experimentId={experiment.id} selectedRevisionId={revisionId} onSelect={setRevisionId} />
  )

  let body
  if (replicatesQuery.isError || !cells) {
    body = <p className="text-sm text-muted-foreground">Could not load this experiment's cells.</p>
  } else if (cells.length === 0) {
    body = (
      <p className="text-sm text-muted-foreground">
        {viewingHistory
          ? 'This design revision has no cells.'
          : 'No cells yet — generate this experiment’s design from the Design tab first.'}
      </p>
    )
  } else {
    body = <CellsBody experiment={experiment} cells={cells} />
  }

  // A superseded revision is a record of what was, not something to keep
  // working in -- say so plainly, since every control around it (CSV, the
  // canvas's own Run buttons) still acts on the current design.
  const historyBanner = viewingHistory ? (
    <div className="space-y-2">
      <p className="rounded-md border border-amber-500/50 bg-amber-500/10 px-3 py-2 text-sm font-medium text-amber-950 dark:text-amber-200">
        <span className="font-semibold">Viewing a superseded design revision — read-only history.</span>{' '}
        The canvas and CSV export still use the current design.
      </p>
      <Button size="sm" variant="outline" onClick={() => setRevisionId(null)}>
        <ArrowLeft className="size-3.5" />
        Back to current design
      </Button>
    </div>
  ) : null

  if (fullscreen) {
    return (
      <div className="fixed inset-0 z-50 flex flex-col bg-background p-4">
        <div className="mb-3 flex items-center justify-between">
          <p className="text-sm font-semibold">
            Cells — <span className="font-mono text-muted-foreground">{cellCount} {cellCount === 1 ? 'cell' : 'cells'} · {scoredReplicates}/{cells?.length ?? 0} replicates scored</span>
          </p>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => void handleDownloadCsv()}>
              <Download className="size-3.5" /> Download CSV
            </Button>
            <Button variant="outline" size="icon-sm" aria-label="Exit fullscreen" onClick={() => setFullscreen(false)}>
              <Minimize2 className="size-4" />
            </Button>
          </div>
        </div>
        <div className="flex-1 space-y-3 overflow-y-auto text-sm">
          {historyBanner}
          {body}
          {history}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3 p-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <span className="font-mono text-xs text-muted-foreground">
          {cellCount} {cellCount === 1 ? 'cell' : 'cells'} · {scoredReplicates}/{cells?.length ?? 0} replicates scored
        </span>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="icon-sm" aria-label="Download replicates CSV" onClick={() => void handleDownloadCsv()}>
            <Download className="size-3.5" />
          </Button>
          <Button variant="outline" size="sm" onClick={() => setFullscreen(true)}>
            <Maximize2 className="size-3.5" /> Maximize
          </Button>
        </div>
      </div>
      {historyBanner}
      {body}
      {history}
    </div>
  )
}
