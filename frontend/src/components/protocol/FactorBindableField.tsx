import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { experimentsApi } from '@/api/client'
import { FactorEditorDialog } from './FactorEditorDialog'
import { parseLevelValue, type LevelType } from './factorLevels'
import type { DesignFactor } from '@/types/experiments'

// Seeds a fresh factor's first level with the field's own current value --
// e.g. binding an agent's already-written system prompt starts that factor
// at "the prompt you already have, plus whatever alternates you want to
// try" rather than making you retype it just to get back to today's value.
// Only matters at creation time: once bound, this component only ever shows
// the badge (see the `boundFactorName` branch below), never the popover/
// dialog again.
function seedLevels(currentValue: unknown): string[] {
  const first = currentValue !== null && currentValue !== undefined && currentValue !== '' ? String(currentValue) : ''
  return [first, '']
}

// Wraps a field's own Label+control (passed as children) with either a "+"
// trigger (unbound) or a "Factor: {name}" badge + remove action (bound).
// The factor itself lives on the linked experiment's design_spec.factors --
// this component only owns the quick-create UI for declaring/removing one,
// plus the node-side half of the binding (factor_bindings[fieldPath]) via
// onBind/onUnbind, which the caller wires into its own node.data update.
//
// 'text' levelType (a long-form value, e.g. a full system prompt) escalates
// straight to FactorEditorDialog instead of this popover's own one-line
// Input rows -- string/number/boolean keep the popover, since it's already
// adequate for short values and a handful of levels; rebuilding a UI that
// already works for the common case would be disproportionate.
export function FactorBindableField({
  experimentId,
  fieldPath,
  defaultLabel,
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
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [factorName, setFactorName] = useState(defaultLabel)
  const [levels, setLevels] = useState<string[]>(() => seedLevels(currentValue))
  const queryClient = useQueryClient()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId && open,
  })

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
    return (
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <Badge variant="outline" className="gap-1">
          Factor: {boundFactorName}
          <button type="button" onClick={onUnbind} aria-label="Remove factor binding" className="cursor-pointer hover:text-destructive">
            <X className="size-3" />
          </button>
        </Badge>
      </div>
    )
  }

  if (!experimentId) {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <span title="This protocol has no linked experiment yet, so it has nothing to bind a factor to.">
          <Button variant="ghost" size="icon-sm" disabled aria-label="Make experimental factor">
            <Plus className="size-3.5" />
          </Button>
        </span>
      </div>
    )
  }

  if (levelType === 'text') {
    return (
      <div className="flex flex-wrap items-center gap-2">
        {children}
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Make experimental factor"
          title="Make experimental factor"
          onClick={() => setOpen(true)}
        >
          <Plus className="size-3.5" />
        </Button>
        <FactorEditorDialog
          open={open}
          onOpenChange={setOpen}
          factor={{ name: defaultLabel, levels: seedLevels(currentValue), level_type: 'text' }}
          onSave={(next) => saveMutation.mutate(next)}
        />
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      {children}
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (next) {
            setFactorName(defaultLabel)
            setLevels(seedLevels(currentValue))
          }
        }}
      >
        <PopoverTrigger
          render={
            <Button variant="ghost" size="icon-sm" aria-label="Make experimental factor" title="Make experimental factor">
              <Plus className="size-3.5" />
            </Button>
          }
        />
        <PopoverContent className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor={`${fieldPath}-factor-name`}>Factor name</Label>
            <Input id={`${fieldPath}-factor-name`} value={factorName} onChange={(e) => setFactorName(e.target.value)} />
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
            disabled={saveMutation.isPending || !factorName.trim()}
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
      </Popover>
    </div>
  )
}
