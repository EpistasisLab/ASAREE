import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import type { NodeConfigIssue } from './nodeConfigIssues'

// Shown when the Run button's own pre-flight scan (findNodeConfigIssues)
// finds at least one obviously misconfigured node -- lets the user run
// anyway (the scan is best-effort, not exhaustive) rather than hard-
// blocking Run outright, but makes the likely-to-fail nodes visible before
// spending a real run attempt on them instead of only finding out after a
// generic "one or more nodes failed" status. Matches
// DeleteNodeConfirmDialog's own shell (Dialog/DialogFooter, Cancel + one
// other action) for the same reason that exists: a click shouldn't silently
// commit to something without a chance to back out.
export function RunWithIssuesDialog({
  issues,
  onCancel,
  onRunAnyway,
}: {
  issues: NodeConfigIssue[]
  onCancel: () => void
  onRunAnyway: () => void
}) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>
            {issues.length === 1 ? '1 node has an unresolved issue' : `${issues.length} nodes have unresolved issues`}
          </DialogTitle>
          <DialogDescription>Running now will likely fail on these -- fix them first, or run anyway.</DialogDescription>
        </DialogHeader>
        <ul className="max-h-48 space-y-1.5 overflow-y-auto text-sm">
          {issues.map((issue) => (
            <li key={issue.nodeId} className="rounded-md border px-2.5 py-1.5">
              <p className="truncate font-medium" title={issue.label}>
                {issue.label}
              </p>
              <p className="text-xs text-muted-foreground">{issue.issues.join('; ')}</p>
            </li>
          ))}
        </ul>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button onClick={onRunAnyway}>Run anyway</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
