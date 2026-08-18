import type { RefObject } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Card } from '@/components/ui/card'
import { Skeleton } from '@/components/ui/skeleton'
import { CellsTab } from './cells/CellsTab'
import { DesignTab } from './DesignTab'
import type { ProtocolCanvasHandle } from './ProtocolCanvas'
import { ResultsTab } from './ResultsTab'
import { RunsTab } from './RunsTab'
import type { Experiment } from '@/types/experiments'

// A fixed left panel on the protocol canvas -- the primary place to build
// and monitor an experiment (Design/Cells/Runs/Results), replacing the
// previous edge-to-edge canvas with no persistent experiment context, and
// now also the only home for what used to be a separate static experiment
// detail page (the cells heatmap/table, the per-agent run tally). First
// page-level use of components/ui/tabs.tsx (previously only inside one
// node's own inspector, AgentNodeInspector.tsx) -- same tab-group idiom,
// just at the page layout level instead of a floating dialog.
export function ExperimentSidePanel({
  experiment,
  protocolId,
  canvasRef,
  isLoading,
}: {
  experiment: Experiment | undefined
  protocolId: string | undefined
  canvasRef: RefObject<ProtocolCanvasHandle | null>
  isLoading: boolean
}) {
  return (
    <Card className="flex min-h-0 w-96 shrink-0 flex-col overflow-hidden p-0">
      {isLoading || !experiment ? (
        <div className="space-y-3 p-3">
          <Skeleton className="h-8 w-full" />
          <Skeleton className="h-32 w-full" />
        </div>
      ) : (
        <Tabs defaultValue="design" className="flex h-full min-h-0 flex-col">
          <TabsList className="mx-3 mt-3 shrink-0">
            <TabsTrigger value="design">Design</TabsTrigger>
            {/* Between Design and Runs, in the order the work actually happens:
                declare the design, look at the cells it produced, watch them
                run, then read the analysis. Deliberately NOT folded into
                Results -- that tab is the statistical analysis OF these
                numbers (effects, CIs, non-inferiority), not the raw grid. */}
            <TabsTrigger value="cells">Cells</TabsTrigger>
            <TabsTrigger value="runs">Runs</TabsTrigger>
            <TabsTrigger value="results">Results</TabsTrigger>
          </TabsList>

          {/* min-h-0 is load-bearing here -- without it, a flex item's
              default min-height:auto keeps this box as tall as its content,
              so on a short viewport the panel silently overflows the page
              instead of scrolling. overflow-y-auto lives directly on each
              TabsContent (the one bounded box), not on a nested div inside
              it, so there's exactly one scroll container per tab. */}
          <TabsContent value="design" className="min-h-0 flex-1 overflow-y-auto">
            <DesignTab experiment={experiment} protocolId={protocolId} canvasRef={canvasRef} />
          </TabsContent>

          <TabsContent value="cells" className="min-h-0 flex-1 overflow-y-auto">
            <CellsTab experiment={experiment} />
          </TabsContent>

          <TabsContent value="runs" className="min-h-0 flex-1 overflow-y-auto">
            {protocolId ? (
              <RunsTab experimentId={experiment.id} protocolId={protocolId} />
            ) : (
              <p className="p-3 text-sm text-muted-foreground">This experiment has no protocol yet.</p>
            )}
          </TabsContent>

          <TabsContent value="results" className="min-h-0 flex-1 overflow-y-auto">
            <ResultsTab experimentId={experiment.id} />
          </TabsContent>
        </Tabs>
      )}
    </Card>
  )
}
