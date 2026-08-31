import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'

// Kept separate from RunConfirmDialog because this is a hard stop before a
// run can be reviewed: generated cells no longer represent the saved canvas
// or design declaration, so there is no safe "run anyway" path.
export function DesignRegenerationRequiredDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent showCloseButton={false} className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Design changed — regeneration required</DialogTitle>
          <DialogDescription>
            The generated cells no longer match the canvas or design settings you are viewing.
          </DialogDescription>
        </DialogHeader>
        <p className="text-sm text-muted-foreground">
          Review the Design tab and regenerate before running. This includes canvas changes and changes to factors, levels, or replicates.
        </p>
        <DialogFooter>
          <Button onClick={onClose}>Review design</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
