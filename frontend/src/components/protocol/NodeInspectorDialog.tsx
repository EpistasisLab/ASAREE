import type { ReactNode } from 'react'
import { Dialog, DialogContent } from '@/components/ui/dialog'

// n8n's own Node Detail View (`.dialog` in NodeDetailsView.vue) is a large,
// FIXED-size floating frame -- `width`/`height` are both
// `calc(100% - var(--spacing--2xl))` with a `var(--spacing--lg)` margin from
// the viewport edges, not a box that grows/shrinks to fit whichever of its
// Parameters/Settings/Docs tabs is active. The frame never resizes on tab
// switch; only the panels inside it scroll independently. Mirrored here with
// viewport units since this Popup is `position: fixed` (see dialog.tsx):
// a constant ~2rem margin on every side, applied identically regardless of
// node type or which tab/field count is showing.
//
// Shared by every node inspector (Agent/CriticGate/McpTool) so switching
// between node types -- or between an inspector's own tabs -- never changes
// the dialog's footprint. Don't tune this per inspector; adjust it once here.
export const NODE_INSPECTOR_CONTENT_CLASSNAME =
  'flex h-[calc(100vh-4rem)] w-[calc(100vw-4rem)] max-w-[calc(100vw-4rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[calc(100vw-4rem)]'

/** Shared fixed-frame shell for node inspectors: a non-scrolling header row
 * up top (icon/title/Delete/Close, same across all three inspectors) and a
 * `flex-1 overflow-y-auto` body below it that absorbs all of a tab's or a
 * field set's overflow -- the outer frame itself never grows, shrinks, or
 * scrolls as a whole. This is the layout half of the n8n-NDV mirroring
 * described on `NODE_INSPECTOR_CONTENT_CLASSNAME` above; that constant is
 * exported separately in case a future inspector needs the sizing without
 * this exact two-slot structure. */
export function NodeInspectorDialog({
  open,
  onOpenChange,
  header,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  header: ReactNode
  children: ReactNode
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className={NODE_INSPECTOR_CONTENT_CLASSNAME}>
        <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">{header}</div>
        <div className="flex-1 space-y-4 overflow-y-auto p-4">{children}</div>
      </DialogContent>
    </Dialog>
  )
}
