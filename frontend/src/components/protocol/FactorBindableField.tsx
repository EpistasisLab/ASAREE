import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { experimentsApi } from '@/api/client'
import type { DesignFactor } from '@/types/experiments'

type LevelType = 'number' | 'string' | 'boolean'

function parseLevel(raw: string, type: LevelType): unknown {
  return type === 'number' ? Number(raw) : raw
}

// Wraps a field's own Label+control (passed as children) with either a "+"
// trigger (unbound) or a "Factor: {name}" badge + remove action (bound).
// The factor itself lives on the linked experiment's design_spec.factors --
// this component only owns the popover UI for declaring/removing one, plus
// the node-side half of the binding (factor_bindings[fieldPath]) via
// onBind/onUnbind, which the caller wires into its own node.data update.
export function FactorBindableField({
  experimentId,
  fieldPath,
  defaultLabel,
  levelType,
  boundFactorName,
  onBind,
  onUnbind,
  children,
}: {
  experimentId: string | null
  fieldPath: string
  defaultLabel: string
  levelType: LevelType
  boundFactorName?: string
  onBind: (factorName: string) => void
  onUnbind: () => void
  children: React.ReactNode
}) {
  const [open, setOpen] = useState(false)
  const [factorName, setFactorName] = useState(defaultLabel)
  const [levels, setLevels] = useState<string[]>(['', ''])
  const queryClient = useQueryClient()

  const experimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId && open,
  })

  const saveMutation = useMutation({
    mutationFn: async () => {
      const experiment = experimentQuery.data ?? (await experimentsApi.get(experimentId!))
      const existingFactors = experiment.design_spec?.factors ?? []
      const parsedLevels: unknown[] =
        levelType === 'boolean' ? [true, false] : levels.filter((l) => l.trim() !== '').map((l) => parseLevel(l, levelType))
      const nextFactors: DesignFactor[] = [
        ...existingFactors.filter((f) => f.name !== factorName),
        { name: factorName, levels: parsedLevels },
      ]
      await experimentsApi.update(experimentId!, { design_spec: { ...experiment.design_spec, factors: nextFactors } })
    },
    onSuccess: () => {
      onBind(factorName)
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
          <button type="button" onClick={onUnbind} aria-label="Remove factor binding" className="hover:text-destructive">
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

  return (
    <div className="flex flex-wrap items-center gap-2">
      {children}
      <Popover
        open={open}
        onOpenChange={(next) => {
          setOpen(next)
          if (next) {
            setFactorName(defaultLabel)
            setLevels(['', ''])
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
                Add level
              </Button>
            </div>
          )}
          <Button size="sm" className="w-full" disabled={saveMutation.isPending || !factorName.trim()} onClick={() => saveMutation.mutate()}>
            Save
          </Button>
        </PopoverContent>
      </Popover>
    </div>
  )
}
