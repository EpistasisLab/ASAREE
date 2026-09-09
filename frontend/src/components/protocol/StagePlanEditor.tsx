import { ArrowDown, ArrowUp, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { STAGE_GATE_RULES, type StagePlanStage } from '@/types/experiments'
import { stagePlanIssues, tabularMlStages, toggleGateRule } from '@/lib/stagePlan'
import { InfoTooltip } from './InfoTooltip'

// The staged pipeline a dataset workspace runs through, declared per
// experiment. It used to be three hardcoded stages (data cleaning → feature
// transformation → feature selection), so any other kind of staged work got an
// empty workspace and "unknown stage" from the tools.
//
// Two shapes, deliberately: the built-in preset -- which is what the published
// tabular-ML pipeline is, and stays immutable -- or a custom list. Choosing
// custom COPIES the preset's stages rather than editing it, because a
// published result depends on what its stages meant.
//
// Width-responsive via container queries rather than a media query: this lives
// in ExperimentSidePanel, which the user drags between 320px and 1100px, so
// what matters is the panel's width and not the viewport's.

const PRESET_VALUE = 'tabular_ml'
const CUSTOM_VALUE = 'custom'

export function StagePlanEditor({
  custom,
  stages,
  disabled = false,
  onCustomChange,
  onStagesChange,
}: {
  custom: boolean
  stages: StagePlanStage[]
  disabled?: boolean
  onCustomChange: (custom: boolean) => void
  onStagesChange: (stages: StagePlanStage[]) => void
}) {
  const issues = stagePlanIssues(stages)

  function update(index: number, patch: Partial<StagePlanStage>) {
    onStagesChange(stages.map((stage, i) => (i === index ? { ...stage, ...patch } : stage)))
  }

  function move(index: number, delta: number) {
    const target = index + delta
    if (target < 0 || target >= stages.length) return
    const next = [...stages]
    ;[next[index], next[target]] = [next[target], next[index]]
    onStagesChange(next)
  }

  function add() {
    // A new stage starts ungated: the universal train/test consistency checks
    // still apply, and guessing at a rule the user hasn't asked for would fail
    // promotions for reasons they never declared.
    const suffix = stages.length + 1
    onStagesChange([...stages, { id: `stage_${suffix}`, label: `Stage ${suffix}`, gate: {} }])
  }

  return (
    <div className="@container space-y-2">
      <Select
        value={custom ? CUSTOM_VALUE : PRESET_VALUE}
        disabled={disabled}
        onValueChange={(value) => {
          if (!value) return
          const nextCustom = value === CUSTOM_VALUE
          // Seed the custom list from the preset so it opens on something that
          // already works, and restore the preset's stages when switching back
          // -- neither direction loses a pipeline that ran.
          if (nextCustom !== custom) onStagesChange(tabularMlStages())
          onCustomChange(nextCustom)
        }}
      >
        <SelectTrigger className="w-full" disabled={disabled}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={PRESET_VALUE}>Tabular ML (default)</SelectItem>
          <SelectItem value={CUSTOM_VALUE}>Custom stages</SelectItem>
        </SelectContent>
      </Select>

      {!custom && (
        <>
          <p className="text-xs text-muted-foreground">
            The three-stage pipeline the platform ships with. Each stage gets a scratch area the agent iterates in,
            then promotes to a versioned artifact the next stage reads.
          </p>
          <ol className="space-y-1">
            {stages.map((stage, index) => (
              <li
                key={stage.id}
                className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md border bg-muted/20 px-2.5 py-2 text-sm"
              >
                <span className="font-mono text-xs text-muted-foreground">{index + 1}</span>
                <span className="min-w-0 flex-1 truncate">{stage.label}</span>
                <span className="font-mono text-xs text-[color:var(--card-accent,var(--primary))]">
                  {stage.version_id}
                </span>
              </li>
            ))}
          </ol>
        </>
      )}

      {custom && (
        <>
          <ol className="space-y-2">
            {stages.map((stage, index) => (
              <StageRow
                key={index}
                stage={stage}
                index={index}
                total={stages.length}
                disabled={disabled}
                onChange={(patch) => update(index, patch)}
                onMove={(delta) => move(index, delta)}
                onRemove={() => onStagesChange(stages.filter((_, i) => i !== index))}
              />
            ))}
          </ol>
          <Button variant="outline" size="sm" disabled={disabled} onClick={add}>
            <Plus className="size-3.5" /> Add stage
          </Button>
          {issues.length > 0 && (
            <div className="rounded-md border border-destructive/40 bg-destructive/10 px-2.5 py-2 text-xs text-destructive">
              <ul className="list-disc space-y-0.5 pl-4">
                {issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            </div>
          )}
          {/* The change is saved either way; the consequence is that cells
              staged through the old pipeline aren't comparable to the new
              one's, which is why this is amber rather than destructive. The
              workspaces themselves refuse to restage: a cell's pipeline is
              fixed the first time it is opened. */}
          <p className="rounded-md border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/10 px-2.5 py-2 text-xs text-muted-foreground">
            Changing the stages means every cell would run a different pipeline, so the cells need regenerating before
            their results are comparable. Workspaces already staged through the old stages keep them.
          </p>
        </>
      )}
    </div>
  )
}

function StageRow({
  stage,
  index,
  total,
  disabled,
  onChange,
  onMove,
  onRemove,
}: {
  stage: StagePlanStage
  index: number
  total: number
  disabled: boolean
  onChange: (patch: Partial<StagePlanStage>) => void
  onMove: (delta: number) => void
  onRemove: () => void
}) {
  const gate = stage.gate ?? {}
  return (
    <li className="space-y-2 rounded-md border bg-muted/20 px-2.5 py-2">
      {/* One column until the panel is wide enough for the id beside the name
          -- at 320px they'd each be unusably narrow. */}
      <div className="flex flex-col gap-2 @sm:flex-row @sm:items-center">
        <span className="font-mono text-xs text-muted-foreground @sm:w-4">{index + 1}</span>
        <Input
          aria-label={`Stage ${index + 1} name`}
          className="min-w-0 flex-1"
          value={stage.label}
          disabled={disabled}
          placeholder="Stage name"
          onChange={(event) => onChange({ label: event.target.value })}
        />
        <Input
          aria-label={`Stage ${index + 1} id`}
          className="min-w-0 font-mono text-xs @sm:w-32"
          value={stage.id}
          disabled={disabled}
          placeholder="stage_id"
          onChange={(event) => onChange({ id: event.target.value.toLocaleLowerCase().replace(/\s+/g, '_') })}
        />
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Move ${stage.label || `stage ${index + 1}`} earlier`}
            disabled={disabled || index === 0}
            onClick={() => onMove(-1)}
          >
            <ArrowUp className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Move ${stage.label || `stage ${index + 1}`} later`}
            disabled={disabled || index === total - 1}
            onClick={() => onMove(1)}
          >
            <ArrowDown className="size-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            aria-label={`Remove ${stage.label || `stage ${index + 1}`}`}
            disabled={disabled || total === 1}
            onClick={onRemove}
          >
            <X className="size-3.5" />
          </Button>
        </div>
      </div>

      {/* Derived, never typed: it's a path component in the workspace's
          version lineage, so a hand-typed one buys nothing and a typo in one
          is a broken pipeline. */}
      <p className="font-mono text-xs text-muted-foreground">
        writes <span className="text-[color:var(--card-accent,var(--primary))]">v{index + 1}_{stage.id || '…'}</span>
        {index === 0 ? ' from the raw dataset' : ` from v${index}_…`}
      </p>

      <div className="space-y-1">
        <Label className="flex items-center gap-1.5 text-xs">
          Promotion gate
          <InfoTooltip>
            Structural checks run before this stage's work is promoted to a versioned artifact. Train/test column
            consistency and a present target are always checked; these are the extra rules this stage declares. A
            stage with none still gets those two.
          </InfoTooltip>
        </Label>
        {STAGE_GATE_RULES.map((rule) => (
          <label key={`${rule.key}:${rule.value}`} className="flex items-start gap-2 text-xs text-muted-foreground">
            <Checkbox
              className="mt-0.5"
              checked={gate[rule.key] === rule.value}
              disabled={disabled}
              onCheckedChange={() => onChange({ gate: toggleGateRule(gate, rule.key, rule.value) })}
            />
            <span>
              <span className="text-foreground">{rule.label}</span> — {rule.description}
            </span>
          </label>
        ))}
      </div>

      <label className="flex items-start gap-2 text-xs text-muted-foreground">
        <Checkbox
          className="mt-0.5"
          checked={stage.fixed_input === true}
          disabled={disabled}
          onCheckedChange={(checked) => onChange({ fixed_input: checked === true })}
        />
        <span>
          <span className="text-foreground">Re-reads one fixed input</span> — the stage always sees the previous
          stage's accepted output rather than its own working copy, so its tools can be called in any order (the
          preset's feature-selection stage works this way).
        </span>
      </label>
    </li>
  )
}
