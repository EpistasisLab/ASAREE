import type { Node } from '@xyflow/react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

function nodeLabel(node: Node): string {
  return (node.data as { label?: string })?.label || 'this node'
}

// One shared confirmation for every way a node can be removed -- the hover
// toolbar's trash icon and the Backspace/Delete key both go through
// xyflow's own deleteElements (ProtocolCanvas.tsx's onBeforeDelete), while
// the node inspector's own Delete button calls requestDeleteNode directly;
// both routes render this same dialog rather than duplicating the confirm
// UI. Matches ProtocolCanvasMenu.tsx's own DeleteConfirmDialog convention
// (Dialog/DialogContent/DialogFooter, Cancel + destructive Delete) for the
// same reason that one exists: a click or keystroke shouldn't be able to
// silently discard part of a protocol.
export function DeleteNodeConfirmDialog({ nodes, onCancel, onConfirm }: { nodes: Node[]; onCancel: () => void; onConfirm: () => void }) {
  const isSingle = nodes.length === 1

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) onCancel()
      }}
    >
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{isSingle ? 'Delete this node?' : `Delete ${nodes.length} nodes?`}</DialogTitle>
          <DialogDescription>
            {isSingle ? `"${nodeLabel(nodes[0])}"` : `These ${nodes.length} nodes`} and their connections will be removed from the
            canvas. This can't be undone.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={onCancel}>
            Cancel
          </Button>
          <Button variant="destructive" onClick={onConfirm}>
            Delete
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
