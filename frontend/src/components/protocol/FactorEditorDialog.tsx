import { useEffect, useState } from 'react'
import { Plus, Variable, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { cardAccent, cn, hashToChartHue, HUD_ACCENT_RING_CLASSNAME } from '@/lib/utils'
import type { UnboundField } from './bindableFields'
import { computeFactorName, LEVEL_TYPE_LABELS, levelTypeOf, parseLevelValue, seedLevels, type LevelType } from './factorLevels'
import { NODE_INSPECTOR_CONTENT_CLASSNAME } from './NodeInspectorDialog'
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
  pickableFields,
  existingNames,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  factor: DesignFactor
  // Only passed by DesignTab's "Add factor" entry point -- every bindable
  // field on the canvas that isn't already bound to something. Lives here
  // (not a separate small popover before this dialog even opens) since a
  // canvas can realistically have a large number of fields to search
  // through, and this dialog already has the room a cramped popover
  // wouldn't. FactorBindableField's own quick-create (already scoped to one
  // specific field) and DesignTab's "Edit" on an existing factor both omit
  // this -- there's nothing to pick, the field is already known.
  pickableFields?: UnboundField[]
  // Needed to dedupe the computed name once a field is picked -- only
  // meaningful alongside pickableFields.
  existingNames?: string[]
  // `field` is only present when this save came from picking one of
  // pickableFields -- the caller uses it to write the binding onto the
  // actual canvas node (this dialog has no way to do that itself).
  onSave: (factor: DesignFactor, field?: UnboundField) => void
}) {
  const [selectedField, setSelectedField] = useState<UnboundField | null>(null)
  const [search, setSearch] = useState('')
  const [levelType, setLevelType] = useState<LevelType>(levelTypeOf(factor))
  const [levels, setLevels] = useState<string[]>(factor.levels.map((l) => String(l)))

  // Re-seed the local draft every time this dialog (re)opens -- same
  // "re-seed on open" convention DesignTab.tsx and FactorBindableField.tsx's
  // popover already use. In field-picker mode there's nothing to seed yet
  // until a field is actually picked (see pickField below).
  useEffect(() => {
    if (open) {
      setSelectedField(null)
      setSearch('')
      if (!pickableFields) {
        setLevelType(levelTypeOf(factor))
        setLevels(factor.levels.map((l) => String(l)))
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, factor, pickableFields])

  function pickField(field: UnboundField) {
    setSelectedField(field)
    setLevelType(field.levelType)
    setLevels(seedLevels(field.currentValue))
  }

  function changeLevelType(next: LevelType) {
    setLevelType(next)
    // Boolean levels are fixed ([true, false], nothing to type); leaving
    // boolean has nothing meaningful to recover into either -- both
    // directions just reset to that type's own default starting levels.
    if (next === 'boolean' || levelType === 'boolean') {
      setLevels((next === 'boolean' ? [true, false] : ['', '']).map((l) => String(l)))
    }
  }

  // Computed, not user-typed (see factorLevels.ts's computeFactorName) --
  // standardized so the same field label on two different nodes (e.g. two
  // Agents' own "System prompt") can never collide into one shared factor
  // by accident. Outside field-picker mode this is just whatever was
  // already computed when the factor was first bound to its field.
  const name = pickableFields ? (selectedField ? computeFactorName(selectedField.nodeLabel, selectedField.fieldLabel, existingNames ?? []) : '') : factor.name
  const needsFieldPick = !!pickableFields && !selectedField

  function save() {
    const parsedLevels =
      levelType === 'boolean' ? [true, false] : levels.filter((l) => l.trim() !== '').map((l) => parseLevelValue(l, levelType))
    onSave({ name, levels: parsedLevels, level_type: levelType }, selectedField ?? undefined)
    onOpenChange(false)
  }

  const accent = hashToChartHue(name || 'factor')
  const filteredFields = (pickableFields ?? []).filter((f) =>
    `${f.nodeLabel}: ${f.fieldLabel}`.toLowerCase().includes(search.trim().toLowerCase()),
  )

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
            <h2 className="text-lg font-semibold">{name || 'New factor'}</h2>
          </div>
          <Button variant="outline" size="icon" aria-label="Close" onClick={() => onOpenChange(false)}>
            <X className="size-4" />
          </Button>
        </div>

        <div className="flex-1 space-y-4 overflow-y-auto p-4">
          {needsFieldPick ? (
            <div className="space-y-1.5">
              <Label>Bind to a field on the canvas</Label>
              <Input autoFocus placeholder="Search fields…" value={search} onChange={(e) => setSearch(e.target.value)} />
              <div className="max-h-96 space-y-0.5 overflow-y-auto rounded-lg border p-1.5">
                {filteredFields.length === 0 && (
                  <p className="py-4 text-center text-sm text-muted-foreground">
                    {pickableFields?.length === 0 ? 'Every bindable field on the canvas is already a factor.' : 'No matching fields.'}
                  </p>
                )}
                {filteredFields.map((field) => (
                  <button
                    key={`${field.nodeId}.${field.fieldPath}`}
                    type="button"
                    onClick={() => pickField(field)}
                    className="flex w-full cursor-pointer items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-sm hover:bg-muted"
                  >
                    <span className="truncate">
                      {field.nodeLabel}: {field.fieldLabel}
                    </span>
                    <Badge variant="outline" className="shrink-0">
                      {LEVEL_TYPE_LABELS[field.levelType]}
                    </Badge>
                  </button>
                ))}
              </div>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-1.5">
                  <Label>Factor name</Label>
                  <p className="rounded-md border border-dashed px-2.5 py-1.5 text-sm text-muted-foreground">{name}</p>
                  {pickableFields && (
                    <button
                      type="button"
                      className="cursor-pointer text-xs text-muted-foreground underline hover:text-foreground"
                      onClick={() => setSelectedField(null)}
                    >
                      Change field
                    </button>
                  )}
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
            </>
          )}
        </div>

        <div className="flex shrink-0 items-center justify-end gap-2 border-t bg-muted/50 px-4 py-3">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button disabled={needsFieldPick} onClick={save}>
            Save
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
