import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { experimentsApi } from '@/api/client'
import {
  COORDINATION_STRATEGY_CATALOG,
  type CoordinationStrategySlug,
  type DesignFactor,
  type DesignMetric,
  type Experiment,
} from '@/types/experiments'

function parseLevel(raw: string): unknown {
  const trimmed = raw.trim()
  if (trimmed === '') return undefined
  const n = Number(trimmed)
  return Number.isFinite(n) && trimmed !== '' ? n : trimmed
}

function levelsToText(levels: unknown[]): string {
  return levels.join(', ')
}

function textToLevels(text: string): unknown[] {
  return text
    .split(',')
    .map(parseLevel)
    .filter((v): v is unknown => v !== undefined)
}

// The Design tab's own factors editor -- the canonical place to view/edit
// every declared factor at once. Distinct from FactorBindableField's own
// per-inspector-field "+" popover (still used to create a NEW factor scoped
// to binding one specific node field) -- both read/write the same
// design_spec.factors array, they just serve different entry points.
function FactorsEditor({ factors, onChange }: { factors: DesignFactor[]; onChange: (factors: DesignFactor[]) => void }) {
  return (
    <div className="space-y-2">
      {factors.map((factor, i) => (
        <div key={i} className="flex items-start gap-1.5">
          <Input
            value={factor.name}
            placeholder="Factor name"
            className="w-28 shrink-0"
            onChange={(e) => onChange(factors.map((f, j) => (j === i ? { ...f, name: e.target.value } : f)))}
          />
          <Input
            value={levelsToText(factor.levels)}
            placeholder="Levels (comma-separated)"
            onChange={(e) => onChange(factors.map((f, j) => (j === i ? { ...f, levels: textToLevels(e.target.value) } : f)))}
          />
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label="Remove factor"
            onClick={() => onChange(factors.filter((_, j) => j !== i))}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button variant="outline" size="sm" onClick={() => onChange([...factors, { name: '', levels: [] }])}>
        <Plus className="size-3.5" /> Add factor
      </Button>
    </div>
  )
}

function MetricsEditor({ metrics, onChange }: { metrics: DesignMetric[]; onChange: (metrics: DesignMetric[]) => void }) {
  return (
    <div className="space-y-2">
      {metrics.map((metric, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Input
            value={metric.name}
            placeholder="Metric name"
            className="flex-1"
            onChange={(e) => onChange(metrics.map((m, j) => (j === i ? { ...m, name: e.target.value } : m)))}
          />
          <label className="flex shrink-0 items-center gap-1 text-xs text-muted-foreground" title="Primary metric">
            <input
              type="radio"
              name="primary-metric"
              checked={metric.primary}
              onChange={() => onChange(metrics.map((m, j) => ({ ...m, primary: j === i })))}
            />
            Primary
          </label>
          <Select
            value={metric.direction}
            onValueChange={(value) => {
              if (value) onChange(metrics.map((m, j) => (j === i ? { ...m, direction: value as DesignMetric['direction'] } : m)))
            }}
          >
            <SelectTrigger className="w-32 shrink-0">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="maximize">Maximize</SelectItem>
              <SelectItem value="minimize">Minimize</SelectItem>
            </SelectContent>
          </Select>
          <Button variant="ghost" size="icon-sm" aria-label="Remove metric" onClick={() => onChange(metrics.filter((_, j) => j !== i))}>
            <X className="size-3.5" />
          </Button>
        </div>
      ))}
      <Button
        variant="outline"
        size="sm"
        onClick={() => onChange([...metrics, { name: '', primary: metrics.length === 0, direction: 'maximize' }])}
      >
        <Plus className="size-3.5" /> Add metric
      </Button>
    </div>
  )
}

// The experiment's full design declaration -- consolidates what used to be
// scattered across the canvas toolbar's DesignPreview and had no UI at all
// (hypothesis, replicates, randomization seed, metrics, coordination
// strategy). Edited as one local draft, persisted with a single explicit
// Save (matching FactorBindableField's own explicit-Save convention) rather
// than autosaving every keystroke across this many fields.
export function DesignTab({ experiment }: { experiment: Experiment }) {
  const queryClient = useQueryClient()

  const [hypothesis, setHypothesis] = useState(experiment.hypothesis ?? '')
  const [factors, setFactors] = useState<DesignFactor[]>(experiment.design_spec?.factors ?? [])
  const [replicates, setReplicates] = useState(experiment.design_spec?.replicates ?? 1)
  const [randomizationSeed, setRandomizationSeed] = useState<number | null>(experiment.design_spec?.randomization_seed ?? null)
  const [metrics, setMetrics] = useState<DesignMetric[]>(experiment.design_spec?.metrics ?? [])
  const [coordinationSlug, setCoordinationSlug] = useState<CoordinationStrategySlug>(
    experiment.design_spec?.coordination_strategy?.slug ?? 'sequential',
  )

  // Re-seed local draft when a different experiment loads (or after a
  // successful save round-trips fresh server data back down).
  useEffect(() => {
    setHypothesis(experiment.hypothesis ?? '')
    setFactors(experiment.design_spec?.factors ?? [])
    setReplicates(experiment.design_spec?.replicates ?? 1)
    setRandomizationSeed(experiment.design_spec?.randomization_seed ?? null)
    setMetrics(experiment.design_spec?.metrics ?? [])
    setCoordinationSlug(experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')
  }, [experiment])

  const saveMutation = useMutation({
    mutationFn: () =>
      experimentsApi.update(experiment.id, {
        hypothesis: hypothesis.trim() || null,
        design_spec: {
          ...experiment.design_spec,
          factors: factors.filter((f) => f.name.trim() !== ''),
          replicates,
          randomization_seed: randomizationSeed,
          metrics: metrics.filter((m) => m.name.trim() !== ''),
          coordination_strategy: { slug: coordinationSlug, params: experiment.design_spec?.coordination_strategy?.params ?? {} },
        },
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id] })
      queryClient.invalidateQueries({ queryKey: ['experiments'] })
    },
  })

  const generateMutation = useMutation({
    mutationFn: () => experimentsApi.generateDesign(experiment.id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['experiments', experiment.id, 'cells'] })
    },
  })

  const validFactors = factors.filter((f) => f.name.trim() !== '' && f.levels.length > 0)
  const combinations = validFactors.reduce((acc, f) => acc * Math.max(f.levels.length, 1), 1)
  const totalTrials = validFactors.length > 0 ? combinations * Math.max(replicates, 1) : 0

  const selectedStrategy = COORDINATION_STRATEGY_CATALOG.find((s) => s.slug === coordinationSlug)

  const isDirty =
    hypothesis !== (experiment.hypothesis ?? '') ||
    JSON.stringify(factors) !== JSON.stringify(experiment.design_spec?.factors ?? []) ||
    replicates !== (experiment.design_spec?.replicates ?? 1) ||
    randomizationSeed !== (experiment.design_spec?.randomization_seed ?? null) ||
    JSON.stringify(metrics) !== JSON.stringify(experiment.design_spec?.metrics ?? []) ||
    coordinationSlug !== (experiment.design_spec?.coordination_strategy?.slug ?? 'sequential')

  return (
    <div className="flex flex-col gap-5 p-3 text-sm">
      <div className="space-y-1.5">
        <Label htmlFor="design-hypothesis">Hypothesis</Label>
        <Textarea
          id="design-hypothesis"
          value={hypothesis}
          onChange={(e) => setHypothesis(e.target.value)}
          placeholder="What are you trying to learn from this experiment?"
          className="min-h-20"
        />
      </div>

      <div className="space-y-1.5">
        <Label>Coordination strategy</Label>
        <Select value={coordinationSlug} onValueChange={(value) => value && setCoordinationSlug(value as CoordinationStrategySlug)}>
          <SelectTrigger className="w-full">
            <SelectValue>{() => selectedStrategy?.label ?? coordinationSlug}</SelectValue>
          </SelectTrigger>
          <SelectContent>
            {COORDINATION_STRATEGY_CATALOG.map((s) => (
              <SelectItem key={s.slug} value={s.slug}>
                {s.label}
                {!s.implemented && ' (coming soon)'}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        {selectedStrategy && <p className="text-xs text-muted-foreground">{selectedStrategy.description}</p>}
        {selectedStrategy && !selectedStrategy.implemented && (
          <p className="text-xs text-[color:var(--chart-4)]">
            Not yet implemented -- coming with the ARES pattern migration. Saving this choice declares intent, but running
            this protocol will be rejected until it's backed.
          </p>
        )}
      </div>

      <div className="space-y-1.5">
        <Label>Factors and levels</Label>
        <FactorsEditor factors={factors} onChange={setFactors} />
      </div>

      <div className="space-y-1.5">
        <Label>Design type</Label>
        <p className="rounded-md border border-dashed px-2.5 py-1.5 text-xs text-muted-foreground capitalize">
          {experiment.design_type}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div className="space-y-1.5">
          <Label htmlFor="design-replicates">Replicates</Label>
          <Input
            id="design-replicates"
            type="number"
            min="1"
            value={replicates}
            onChange={(e) => setReplicates(Math.max(1, Number(e.target.value) || 1))}
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="design-seed">Randomization seed</Label>
          <Input
            id="design-seed"
            type="number"
            placeholder="(none)"
            value={randomizationSeed ?? ''}
            onChange={(e) => setRandomizationSeed(e.target.value === '' ? null : Number(e.target.value))}
          />
        </div>
      </div>

      <div className="space-y-1.5">
        <Label>Metrics</Label>
        <MetricsEditor metrics={metrics} onChange={setMetrics} />
      </div>

      <div className="space-y-1.5 rounded-md border bg-muted/30 px-3 py-2">
        <p className="font-mono text-xs text-muted-foreground">
          {validFactors.length} factor{validFactors.length === 1 ? '' : 's'} → {combinations} condition
          {combinations === 1 ? '' : 's'} × {Math.max(replicates, 1)} replicate{replicates === 1 ? '' : 's'} = {totalTrials} total
          trial{totalTrials === 1 ? '' : 's'}
        </p>
        <Button size="sm" variant="outline" disabled={generateMutation.isPending || isDirty || validFactors.length === 0} onClick={() => generateMutation.mutate()}>
          {generateMutation.isPending ? 'Generating…' : 'Generate design'}
        </Button>
        {isDirty && <p className="text-xs text-muted-foreground">Save your changes before generating.</p>}
        {generateMutation.data && <p className="text-xs text-muted-foreground">{generateMutation.data.length} cell(s) total</p>}
      </div>

      <Button disabled={!isDirty || saveMutation.isPending} onClick={() => saveMutation.mutate()}>
        {saveMutation.isPending ? 'Saving…' : 'Save design'}
      </Button>
    </div>
  )
}
