import { useEffect, useState, type ReactNode } from 'react'
import { Trash2, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Label } from '@/components/ui/label'
import { Slider } from '@/components/ui/slider'
import { cardAccent, cn } from '@/lib/utils'

// Midpoint of the slider's own 20-100 range (see below) -- a visible,
// legible starting point that still shows the transparency effect is there,
// rather than opening fully opaque (indistinguishable from "no feature")
// or at the 20 floor (harder to read by default).
const DEFAULT_OPACITY = 60

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
// Shared by every node inspector (Agent/CriticGate/McpTool/Memory/Llm/
// ReasonActPattern/SingleAgentBaselinePattern) so switching between node
// types -- or between an inspector's own tabs -- never changes the dialog's
// footprint. Don't tune this per inspector; adjust it once here.
export const NODE_INSPECTOR_CONTENT_CLASSNAME =
  'flex h-[calc(100vh-4rem)] w-[calc(100vw-4rem)] max-w-[calc(100vw-4rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-[calc(100vw-4rem)]'

/** Shared fixed-frame shell for node inspectors: a non-scrolling header row
 * up top (icon/title, a transparency slider, Delete/Close) and a
 * `flex-1 overflow-y-auto` body below it that absorbs all of a tab's or a
 * field set's overflow -- the outer frame itself never grows, shrinks, or
 * scrolls as a whole. This is the layout half of the n8n-NDV mirroring
 * described on `NODE_INSPECTOR_CONTENT_CLASSNAME` above; that constant is
 * exported separately in case a future inspector needs the sizing without
 * this exact two-slot structure.
 *
 * Glows/rings/corner-brackets in the caller's own `accent` (same mechanism
 * `Card` uses via `--card-accent`, see lib/utils.ts's `cardAccent()`) --
 * `DialogContent`'s default styling (plain `bg-popover`, neutral
 * `ring-foreground/10`) is otherwise the one HUD-themed surface in the app
 * that never got the treatment every Card/Button already has.
 *
 * Delete/Close and the transparency slider are centralized here rather than
 * duplicated in each of the 7 inspector components -- their markup was
 * already byte-for-byte identical across every one of them. `onDelete` is
 * optional: the two execution-pattern inspectors (ReasonAct/
 * SingleAgentBaseline) have no Delete button at all, by deliberate design
 * (an agent's execution pattern must never go to zero -- see those nodes'
 * own comments), so omitting it just renders Close alone. */
export function NodeInspectorDialog({
  open,
  onOpenChange,
  accent,
  title,
  onDelete,
  onClose,
  children,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  accent: string
  title: ReactNode
  onDelete?: () => void
  onClose: () => void
  children: ReactNode
}) {
  // A viewing convenience, not durable state -- resets to DEFAULT_OPACITY
  // every time the dialog opens (the same Dialog instance is reused as
  // `open` toggles between different nodes, so this can't just be a
  // one-time useState initializer).
  const [opacity, setOpacity] = useState(DEFAULT_OPACITY)
  useEffect(() => {
    if (open) setOpacity(DEFAULT_OPACITY)
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        style={{
          ...cardAccent(accent),
          backgroundColor: `color-mix(in oklch, var(--popover) ${opacity}%, transparent)`,
        }}
        className={cn(
          NODE_INSPECTOR_CONTENT_CLASSNAME,
          "ring-1 ring-[color:var(--card-accent,var(--primary))]/15 shadow-[0_0_24px_-12px_var(--card-accent,var(--primary))] before:pointer-events-none before:absolute before:top-1 before:left-1 before:size-3 before:border-t-2 before:border-l-2 before:border-[color:var(--card-accent,var(--primary))]/60 before:content-[''] after:pointer-events-none after:absolute after:right-1 after:bottom-1 after:size-3 after:border-r-2 after:border-b-2 after:border-[color:var(--card-accent,var(--primary))]/60 after:content-['']",
        )}
      >
        <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">{title}</div>
          <div className="flex items-center gap-4">
            <div className="flex items-center gap-2">
              <Label htmlFor="node-inspector-opacity" className="text-xs whitespace-nowrap text-muted-foreground">
                Transparency
              </Label>
              <Slider
                id="node-inspector-opacity"
                className="w-24"
                min={20}
                max={100}
                step={5}
                value={opacity}
                onValueChange={(value) => setOpacity(value as number)}
                aria-label="Dialog transparency"
              />
            </div>
            <div className="flex items-center gap-2 border-l pl-4">
              {onDelete && (
                <Button variant="ghost" size="icon" aria-label="Delete node" onClick={onDelete}>
                  <Trash2 className="size-4" />
                </Button>
              )}
              <Button variant="outline" size="icon" aria-label="Close" onClick={onClose}>
                <X className="size-4" />
              </Button>
            </div>
          </div>
        </div>
        <div className="flex-1 space-y-4 overflow-y-auto p-4">{children}</div>
      </DialogContent>
    </Dialog>
  )
}
