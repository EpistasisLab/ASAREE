import type { QueryClient } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import type { Edge, Node } from '@xyflow/react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { findNodeConfigIssues } from './nodeConfigIssues'
import { summarizeRun, type RunScope } from './runSummary'

function scopeTitle(scope: RunScope): string {
  switch (scope.type) {
    case 'graph':
      return 'Run the full experiment?'
    case 'replicate':
      return scope.title ?? `Run replicate "${scope.label}"?`
    case 'all-cells':
      return scope.pendingReplicateCount === scope.replicateCount
        ? `Run all ${scope.replicateCount} replicates?`
        : `Run ${scope.pendingReplicateCount} pending replicates?`
    case 'selected-cells':
      return scope.title ?? 'Run the experiment?'
    case 'node':
      return `Run "${scope.label}" alone?`
  }
}

// Shown on EVERY Run click -- the main Run button (whole graph or a picked
// replicate), Run all cells, and each agent's own per-node Play icon -- before anything actually
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
  hasUnpublishedChanges = false,
  publishedRevision = null,
  isPublishing = false,
  publishError = null,
  onPublishAndRun,
  additionalContent,
  confirmLabel,
  confirmDisabled = false,
  isConfirming = false,
  confirmError = null,
}: {
  scope: RunScope
  nodes: Node[]
  edges: Edge[]
  queryClient: QueryClient
  onCancel: () => void
  onConfirm: () => void
  hasUnpublishedChanges?: boolean
  publishedRevision?: number | null
  isPublishing?: boolean
  publishError?: string | null
  onPublishAndRun?: () => void
  additionalContent?: ReactNode
  confirmLabel?: string
  confirmDisabled?: boolean
  isConfirming?: boolean
  confirmError?: string | null
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
      <DialogContent showCloseButton={false} className={additionalContent ? 'sm:max-w-xl' : 'sm:max-w-md'}>
        <DialogHeader>
          <DialogTitle>{scopeTitle(scope)}</DialogTitle>
          <DialogDescription>This makes real LLM calls and may incur cost -- review what's about to run before continuing.</DialogDescription>
        </DialogHeader>

        <div className="space-y-2 text-sm">
          {scope.type === 'all-cells' && (
            <p>
              The published canvas will run each pending replicate across {scope.cellCount}{' '}
              {scope.cellCount === 1 ? 'cell' : 'cells'}.
              {scope.replicateCount > scope.pendingReplicateCount
                ? ` ${scope.replicateCount - scope.pendingReplicateCount} previously completed replicates will be skipped.`
                : ''}
            </p>
          )}
          {scope.type === 'selected-cells' && (
            <p>
              The published canvas will run {scope.pendingReplicateCount + scope.rerunReplicateCount} replicate
              {scope.pendingReplicateCount + scope.rerunReplicateCount === 1 ? '' : 's'} across {scope.cellCount}{' '}
              {scope.cellCount === 1 ? 'cell' : 'cells'}.
              {scope.replicateCount - scope.pendingReplicateCount - scope.rerunReplicateCount > 0
                ? ` ${scope.replicateCount - scope.pendingReplicateCount - scope.rerunReplicateCount} previously completed replicate${scope.replicateCount - scope.pendingReplicateCount - scope.rerunReplicateCount === 1 ? '' : 's'} will be skipped.`
                : ''}
            </p>
          )}
          <p>
            {summary.agentCount} agent{summary.agentCount === 1 ? '' : 's'}
            {summary.criticGateCount > 0 ? `, ${summary.criticGateCount} critic gate${summary.criticGateCount === 1 ? '' : 's'}` : ''} will run
            {scope.type === 'all-cells' ? ' per replicate' : ''}.
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
            {/* Worth its own line rather than folding into "MCP tools":
                knowledge is something the agent will WRITE to -- a directory
                for a bundle, a stored file for an uploaded document -- so a
                run is about to modify something that outlives it, the one
                such side effect in this list. */}
            <div>
              <dt className="inline font-medium text-foreground">Knowledge: </dt>
              <dd className="inline">
                {summary.knowledgeSources.length > 0 ? summary.knowledgeSources.join(', ') : 'none wired'}
              </dd>
            </div>
          </dl>
        </div>

        {additionalContent}
        {confirmError && <p className="text-sm text-destructive">{confirmError}</p>}

        {hasUnpublishedChanges && (
          <div className="space-y-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm">
            <p className="font-medium">Canvas has unpublished changes</p>
            <p className="text-xs text-muted-foreground">
              This run will use published canvas v{publishedRevision}. Publish the latest canvas first to run the changes you are viewing.
            </p>
            {publishError && <p className="text-xs text-destructive">{publishError}</p>}
          </div>
        )}

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
          {hasUnpublishedChanges ? (
            <>
              <Button variant="outline" disabled={isPublishing || isConfirming || confirmDisabled} onClick={onConfirm}>
                Run published v{publishedRevision}
              </Button>
              <Button disabled={isPublishing || isConfirming || confirmDisabled} onClick={onPublishAndRun}>
                {isPublishing ? 'Publishing…' : 'Publish & run'}
              </Button>
            </>
          ) : (
            <Button disabled={isConfirming || confirmDisabled} onClick={onConfirm}>
              {isConfirming ? 'Starting…' : confirmLabel ?? (issues.length > 0 ? 'Run anyway' : 'Confirm & run')}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
