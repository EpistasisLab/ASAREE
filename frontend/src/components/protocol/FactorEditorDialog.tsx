import { useEffect, useState } from 'react'
import { Plus, Variable, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { cardAccent, cn, hashToChartHue, HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import { NODE_INSPECTOR_CONTENT_CLASSNAME } from './NodeInspectorDialog'
import { defaultLevelsForType, LEVEL_TYPE_LABELS, levelTypeOf, parseLevelValue, type LevelType } from './factorLevels'
import type { DesignFactor } from '@/types/experiments'

const LEVEL_TYPES: LevelType[] = ['string', 'text', 'number', 'boolean']

// A per-factor editor with real room -- reuses the node inspector's own
// fixed near-fullscreen frame sizing (NODE_INSPECTOR_CONTENT_CLASSNAME) and
// HUD glow/ring/corner-brackets (HUD_ACCENT_RING_CLASSNAME), but is built
// directly on Dialog/DialogContent rather than on NodeInspectorDialog
// itself: that component's transparency slider and onDelete are node-
// specific concepts (peeking at the canvas *around a specific node*,
// deleting *that node*) that don't map onto a factor, which isn't a canvas
// element and may be bound to zero, one, or many node fields scattered
// across the graph. Deleting a factor already has an obvious home (the
// remove button on DesignTab's own summary row), so this dialog only ever
// edits, never deletes.
export function FactorEditorDialog({
  open,
  onOpenChange,
  factor,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  factor: DesignFactor
  onSave: (factor: DesignFactor) => void
}) {
  const [name, setName] = useState(factor.name)
  const [levelType, setLevelType] = useState<LevelType>(levelTypeOf(factor))
  const [levels, setLevels] = useState<string[]>(factor.levels.map((l) => String(l)))

  // Re-seed the local draft every time a (possibly different) factor is
  // opened -- same "re-seed on open" convention DesignTab.tsx and
  // FactorBindableField.tsx's popover already use.
  useEffect(() => {
    if (open) {
      setName(factor.name)
      setLevelType(levelTypeOf(factor))
      setLevels(factor.levels.map((l) => String(l)))
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, factor])

  function changeLevelType(next: LevelType) {
    setLevelType(next)
    // Boolean levels are fixed ([true, false], nothing to type); leaving
    // boolean has nothing meaningful to recover into either -- both
    // directions just reset to that type's own default starting levels.
    if (next === 'boolean' || levelType === 'boolean') {
      setLevels(defaultLevelsForType(next).map((l) => String(l)))
    }
  }

  function save() {
    const parsedLevels =
      levelType === 'boolean' ? [true, false] : levels.filter((l) => l.trim() !== '').map((l) => parseLevelValue(l, levelType))
    onSave({ name, levels: parsedLevels, level_type: levelType })
    onOpenChange(false)
  }

  const accent = hashToChartHue(factor.name || 'factor')

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        showCloseButton={false}
        style={cardAccent(accent)}
        className={cn(NODE_INSPECTOR_CONTENT_CLASSNAME, HUD_ACCENT_RING_CLASSNAME)}
      >
        <div className="flex shrink-0 items-center justify-between border-b px-4 py-3">
          <div className="flex items-center gap-2">
            <Variable className="size-5" style={{ color: accent }} />
            <h2 className="text-lg font-semibold">{name || 'Factor'}</h2>
          </div>
          <Button variant="outline" size="icon" aria-label="Close" onClick={() => onOpenChange(false)}>
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-1.5">
              <Label htmlFor="factor-name">Factor name</Label>
              <Input id="factor-name" value={name} onChange={(e) => setName(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Level type</Label>
              <Select value={levelType} onValueChange={(value) => value && changeLevelType(value as LevelType)}>
                <SelectTrigger className="w-full">
                  <SelectValue>{() => LEVEL_TYPE_LABELS[levelType]}</SelectValue>
                </SelectTrigger>
                <SelectContent>
                  {LEVEL_TYPES.map((t) => (
                    <SelectItem key={t} value={t}>
                      {LEVEL_TYPE_LABELS[t]}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="space-y-1.5">
            <Label>Levels</Label>
            {levelType === 'boolean' ? (
              <p className="text-xs text-muted-foreground">Levels: true, false</p>
            ) : levelType === 'text' ? (
              <div className="space-y-2">
                {levels.map((level, i) => (
                  <div key={i} className="flex items-start gap-1.5">
                    <Textarea
                      rows={6}
                      className="font-mono text-xs"
                      value={level}
                      onChange={(e) => setLevels((ls) => ls.map((l, j) => (j === i ? e.target.value : l)))}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Remove level"
                      onClick={() => setLevels((ls) => ls.filter((_, j) => j !== i))}
                    >
                      <X className="size-3.5" />
                    </Button>
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => setLevels((ls) => [...ls, ''])}>
                  <Plus className="size-3.5" /> Add level
                </Button>
              </div>
            ) : (
              <div className="space-y-1.5">
                {levels.map((level, i) => (
                  <div key={i} className="flex items-center gap-1.5">
                    <Input
                      type={levelType === 'number' ? 'number' : 'text'}
                      value={level}
                      onChange={(e) => setLevels((ls) => ls.map((l, j) => (j === i ? e.target.value : l)))}
                    />
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Remove level"
                      onClick={() => setLevels((ls) => ls.filter((_, j) => j !== i))}
                    >
                      <X className="size-3.5" />
                    </Button>
                  </div>
                ))}
                <Button variant="outline" size="sm" onClick={() => setLevels((ls) => [...ls, ''])}>
                  <Plus className="size-3.5" /> Add level
                </Button>
              </div>
            )}
          </div>
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2 border-t bg-muted/50 px-4 py-3">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={!name.trim()} onClick={save}>
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
