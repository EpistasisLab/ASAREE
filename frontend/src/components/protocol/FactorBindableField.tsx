import { useState, type ReactNode } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Variable, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip'
import { experimentsApi } from '@/api/client'
import { FactorEditorDialog } from './FactorEditorDialog'
import {
  computeFactorName,
  FACTOR_TRIGGER_CLASSNAME,
  isStructuredLevelType,
  parseLevelValue,
  seedLevels,
  seedStructuredLevels,
  type LevelType,
} from './factorLevels'
import type { DesignFactor } from '@/types/experiments'

// Owns the "make experimental factor" trigger's state/mutation/dialog, but
// NOT its layout -- `children` is a render prop that receives the trigger
// element (a "make it a factor" button, a bound "Factor: {name}" badge, or a
// disabled button) and decides where to put it. Callers place it inline
// right next to their own field's Label text (see e.g. LlmNodeInspector's
// "Model" Label) rather than trailing after the whole Label+control block --
// a fixed trailing position reads as decoration bolted onto the end of a
// row; sitting directly beside the text it labels reads as part of the
// field itself.
//
// The trigger is always the Variable icon (lucide's "(x)" glyph) on an
// `outline` button (a visible border/bg at rest, not just on hover) wrapped
// in this app's own themed Tooltip -- explaining what it DOES ("vary this
// across cells"), not just restating its name, and a real Tooltip rather
// than the browser's native `title` (slow, inconsistent chrome -- see
// CanvasControls.tsx's own reasoning for the same swap).
//
// The Agent/Pattern inspectors' own title-row button (opens the per-node
// field picker, rather than binding one specific field the way every
// FactorBindableField instance below does) -- shares the exact same visual
// identity (icon, text, violet accent, Tooltip) so every "this makes a
// factor" control in the app reads as the same kind of thing regardless of
// which of the two entry points it is.
export function MakeNodeFactorButton({ onClick }: { onClick: () => void }) {
  return (
    <TooltipProvider delay={200}>
      <Tooltip>
        <TooltipTrigger render={<Button variant="outline" size="sm" className={FACTOR_TRIGGER_CLASSNAME} aria-label="Make experimental factor" onClick={onClick} />}>
          <Variable className="size-3.5" />
          Make factor
        </TooltipTrigger>
        <TooltipContent>Bind one of this node's fields to an experimental factor</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

// 'text' levelType (a long-form value, e.g. a full system prompt) and the 3
// structured "whole node as a factor" kinds (llm_config/tool_config/pattern
// -- see factorLevels.ts) escalate straight to FactorEditorDialog instead of
// this popover's own one-line Input rows -- string/number/boolean keep the
// popover, since it's already adequate for short values and a handful of
// levels; rebuilding a UI that already works for the common case would be
// disproportionate.
export function FactorBindableField({
  experimentId,
  // No longer read internally (the removed "Factor name" Input used to key
  // its id off this) -- kept in the prop signature since every call site
  // still passes it to document which field this instance guards.
  fieldPath: _fieldPath,
  defaultLabel,
  nodeLabel,
  levelType,
  currentValue,
  levelOptions,
  boundFactorName,
  onBind,
  onUnbind,
  children,
}: {
  experimentId: string | null
  fieldPath: string
  defaultLabel: string
  // The owning node's own current display label (e.g. data.label || 'Agent')
  // -- factor names are computed, not user-typed (see computeFactorName),
  // specifically so the same field label on two different nodes (e.g. two
  // Agents' own "System prompt") never collides into one shared factor.
  nodeLabel: string
  levelType: LevelType
  // The field's own current value, e.g. config.system_prompt -- omitted for
  // a boolean field, since its levels are always the fixed [true, false].
  currentValue?: unknown
  // The field's own already-fetched choices (e.g. LlmNodeInspector's model/
  // effort lists) -- when given, each level row renders as a Select over
  // these exact values instead of a freeform Input, so a factor's levels
  // can never drift from what the field itself actually accepts. Passed
  // down from the caller's own already-cached query data; this component
  // never fetches anything itself. Ignored when empty/omitted (falls back
  // to the plain Input, e.g. Temperature, or Model before any credential
  // exists to discover models from).
  levelOptions?: { value: string; label: string }[]
  boundFactorName?: string
  onBind: (factorName: string) => void
  onUnbind: () => void
  // Receives the trigger/badge element to place -- e.g.
  // `(trigger) => (<Label className="flex items-center gap-1.5">Model{trigger}</Label>)`.
  children: (trigger: ReactNode) => ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [levels, setLevels] = useState<string[]>(() => seedLevels(currentValue))
  const queryClient = useQueryClient()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId && open,
  })

  // Computed, not user-typed -- existingNames only reflects the OTHER
  // factors already declared (this field is always unbound here, see the
  // `boundFactorName` early return above this component's own JSX below),
  // so any collision here is genuinely a different node/field, never this
  // one being re-saved under its own name.
  const existingNames = experimentQuery.data?.design_spec?.factors?.map((f) => f.name) ?? []
  const factorName = computeFactorName(nodeLabel, defaultLabel, existingNames)

  const saveMutation = useMutation({
    mutationFn: async (next: DesignFactor) => {
      const experiment = experimentQuery.data ?? (await experimentsApi.get(experimentId!))
      const existingFactors = experiment.design_spec?.factors ?? []
      const nextFactors: DesignFactor[] = [...existingFactors.filter((f) => f.name !== next.name), next]
      await experimentsApi.update(experimentId!, { design_spec: { ...experiment.design_spec, factors: nextFactors } })
      return next
    },
    onSuccess: (next) => {
      onBind(next.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      setOpen(false)
    },
  })

  if (boundFactorName) {
    return children(
      <Badge variant="outline" className="gap-1">
        Factor: {boundFactorName}
        <button type="button" onClick={onUnbind} aria-label="Remove factor binding" className="cursor-pointer hover:text-destructive">
          <X className="size-3" />
        </button>
      </Badge>,
    )
  }

  if (!experimentId) {
    return children(
      <TooltipProvider delay={200}>
        <Tooltip>
          <TooltipTrigger render={<Button variant="outline" size="sm" disabled className={FACTOR_TRIGGER_CLASSNAME} aria-label="Make experimental factor" />}>
            <Variable className="size-3.5" />
            Make factor
          </TooltipTrigger>
          <TooltipContent>This protocol has no linked experiment yet, so it has nothing to bind a factor to.</TooltipContent>
        </Tooltip>
      </TooltipProvider>,
    )
  }

  const tooltipText = isStructuredLevelType(levelType)
    ? `Vary this node's whole configuration across this experiment's cells`
    : "Vary this field across this experiment's cells"

  if (levelType === 'text' || isStructuredLevelType(levelType)) {
    const structured = isStructuredLevelType(levelType)
    return (
      <>
        {children(
          <TooltipProvider delay={200}>
            <Tooltip>
              <TooltipTrigger
                render={<Button variant="outline" size="sm" className={FACTOR_TRIGGER_CLASSNAME} aria-label="Make experimental factor" onClick={() => setOpen(true)} />}
              >
                <Variable className="size-3.5" />
                Make factor
              </TooltipTrigger>
              <TooltipContent>{tooltipText}</TooltipContent>
            </Tooltip>
          </TooltipProvider>,
        )}
        <FactorEditorDialog
          open={open}
          onOpenChange={setOpen}
          factor={{
            name: factorName,
            levels: structured ? seedStructuredLevels(currentValue, levelType) : seedLevels(currentValue),
            level_type: levelType,
          }}
          onSave={(next) => saveMutation.mutate(next)}
        />
      </>
    )
  }

  return children(
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next)
        if (next) setLevels(seedLevels(currentValue))
      }}
    >
      <TooltipProvider delay={200}>
        <Tooltip>
          <TooltipTrigger
            render={<PopoverTrigger render={<Button variant="outline" size="sm" className={FACTOR_TRIGGER_CLASSNAME} aria-label="Make experimental factor" />} />}
          >
            <Variable className="size-3.5" />
            Make factor
          </TooltipTrigger>
          <TooltipContent>{tooltipText}</TooltipContent>
        </Tooltip>
      </TooltipProvider>
      <PopoverContent className="space-y-3">
        <div className="space-y-1.5">
          <Label>Factor name</Label>
          <p className="rounded-md border border-dashed px-2.5 py-1.5 text-sm text-muted-foreground">{factorName}</p>
        </div>
        {levelType === 'boolean' ? (
          <p className="text-xs text-muted-foreground">Levels: true, false</p>
        ) : (
          <div className="space-y-1.5">
            <Label>Levels</Label>
            {levels.map((level, i) => (
              <div key={i} className="flex items-center gap-1.5">
                {levelOptions && levelOptions.length > 0 ? (
                  <Select
                    value={level || '__none__'}
                    onValueChange={(value) => {
                      if (!value || value === '__none__') return
                      setLevels((ls) => ls.map((l, j) => (j === i ? value : l)))
                    }}
                  >
                    <SelectTrigger className="w-full">
                      <SelectValue>{() => levelOptions.find((o) => o.value === level)?.label ?? (level || 'Select…')}</SelectValue>
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="__none__" disabled>
                        Select…
                      </SelectItem>
                      {levelOptions.map((opt) => {
                        // Grayed out, not removed -- picking the same value
                        // for two levels of one factor is never meaningful
                        // (it wouldn't vary anything between those cells),
                        // but the option should still be visible so it's
                        // clear why it's unavailable rather than silently
                        // missing.
                        const usedElsewhere = levels.some((l, j) => j !== i && l === opt.value)
                        return (
                          <SelectItem key={opt.value} value={opt.value} disabled={usedElsewhere}>
                            {opt.label}
                          </SelectItem>
                        )
                      })}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    type={levelType === 'number' ? 'number' : 'text'}
                    value={level}
                    onChange={(e) => setLevels((ls) => ls.map((l, j) => (j === i ? e.target.value : l)))}
                  />
                )}
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
              Add level
            </Button>
          </div>
        )}
        <Button
          size="sm"
          className="w-full"
          disabled={saveMutation.isPending}
          onClick={() => {
            const parsedLevels =
              levelType === 'boolean'
                ? [true, false]
                : levels.filter((l) => l.trim() !== '').map((l) => parseLevelValue(l, levelType))
            saveMutation.mutate({ name: factorName, levels: parsedLevels, level_type: levelType })
          }}
        >
          Save
        </Button>
      </PopoverContent>
    </Popover>,
  )
}
