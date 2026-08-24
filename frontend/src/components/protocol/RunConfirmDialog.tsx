import type { QueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { findNodeConfigIssues } from './nodeConfigIssues'
import { summarizeRun, type RunScope } from './runSummary'

function scopeTitle(scope: RunScope): string {
  switch (scope.type) {
    case 'graph':
      return 'Run the full experiment?'
    case 'cell':
      return `Run cell "${scope.label}"?`
    case 'node':
      return `Run "${scope.label}" alone?`
  }
}

// Shown on EVERY Run click -- the main Run button (whole graph or a picked
// cell) and each agent's own per-node Play icon -- before anything actually
// fires. Real LLM calls cost money, so this is the one chance to catch
// "this isn't wired the way I think it is" before spending a real attempt
// on it (see the spinal-fusion experiment trace this session, where a run
// "completed" having done nothing real at all). Replaces the old
// RunWithIssuesDialog, which only ever appeared when the pre-flight scan
// found a problem; this always appears, folding that same scan in as a
// "needs attention" section instead of a separate second dialog on top.
export function RunConfirmDialog({
  scope,
  nodes,
  edges,
  queryClient,
  onCancel,
  onConfirm,
}: {
  scope: RunScope
  nodes: Node[]
  edges: Edge[]
  queryClient: QueryClient
  onCancel: () => void
  onConfirm: () => void
}) {
  const summary = summarizeRun(nodes, edges, scope)
  const allIssues = findNodeConfigIssues(nodes, edges, queryClient)
  // A node-scoped run only ever touches that node plus its own directly
  // wired dependencies -- an issue on some unrelated node elsewhere on the
  // canvas isn't relevant to THIS run, so don't show it here.
  const relevantNodeIds = scope.type === 'node' ? new Set([scope.nodeId, ...edges.filter((e) => e.target === scope.nodeId).map((e) => e.source)]) : null
  const issues = relevantNodeIds ? allIssues.filter((issue) => relevantNodeIds.has(issue.nodeId)) : allIssues

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{scopeTitle(scope)}</DialogTitle>
          <DialogDescription>This makes real LLM calls and may incur cost -- review what's about to run before continuing.</DialogDescription>
        </DialogHeader>

        <div className="space-y-2 text-sm">
          <p>
            {summary.agentCount} agent{summary.agentCount === 1 ? '' : 's'}
            {summary.criticGateCount > 0 ? `, ${summary.criticGateCount} critic gate${summary.criticGateCount === 1 ? '' : 's'}` : ''} will run.
          </p>
          <dl className="space-y-1 text-xs text-muted-foreground">
            <div>
              <dt className="inline font-medium text-foreground">Dataset: </dt>
              <dd className="inline">{summary.datasets.length > 0 ? summary.datasets.join(', ') : 'none selected'}</dd>
            </div>
            <div>
              <dt className="inline font-medium text-foreground">Model(s): </dt>
              <dd className="inline">{summary.models.length > 0 ? summary.models.join(', ') : 'none configured'}</dd>
            </div>
            <div>
              <dt className="inline font-medium text-foreground">MCP tools: </dt>
              <dd className="inline">{summary.toolServers.length > 0 ? summary.toolServers.join(', ') : 'none wired'}</dd>
            </div>
            <div>
              <dt className="inline font-medium text-foreground">Skills: </dt>
              <dd className="inline">{summary.skills.length > 0 ? summary.skills.join(', ') : 'none wired'}</dd>
            </div>
            {/* Worth its own line rather than folding into "MCP tools": a
                bundle is a directory the agent will WRITE to, so a run is
                about to modify something on disk outside ASAREE -- the one
                side effect in this list that outlives the run. */}
            <div>
              <dt className="inline font-medium text-foreground">Knowledge: </dt>
              <dd className="inline">
                {summary.knowledgeBundles.length > 0 ? summary.knowledgeBundles.join(', ') : 'none wired'}
              </dd>
            </div>
          </dl>
        </div>

        {issues.length > 0 && (
          <div className="space-y-1.5">
            <p className="text-sm font-medium text-destructive">
              {issues.length === 1 ? '1 node needs attention' : `${issues.length} nodes need attention`}
            </p>
            <ul className="max-h-40 space-y-1.5 overflow-y-auto text-sm">
              {issues.map((issue) => (
                <li key={issue.nodeId} className="rounded-md border border-destructive/30 bg-destructive/10 px-2.5 py-1.5">
                  <p className="truncate font-medium" title={issue.label}>
                    {issue.label}
                  </p>
                  <p className="text-xs text-muted-foreground">{issue.issues.join('; ')}</p>
                </li>
              ))}
            </ul>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={onConfirm}>{issues.length > 0 ? 'Run anyway' : 'Confirm & run'}</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
