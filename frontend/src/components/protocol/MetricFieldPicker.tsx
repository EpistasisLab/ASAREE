import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { projectionLabel, type FieldProjection, type MetricField } from '@/lib/metricFields'

function sameProjection(a: FieldProjection, b: FieldProjection) {
  return a.path === b.path && (a.transform ?? null) === (b.transform ?? null)
}

// Which part of a producer's output a custom metric records: the whole output
// (no picks), or one field per metric. `multi` lets one pass create a metric
// per ticked field; otherwise ticking a field replaces the current pick.
export function MetricFieldPicker({ fields, picks, multi, status, onChange }: {
  fields: MetricField[]
  picks: FieldProjection[]
  multi: boolean
  status?: string
  onChange: (picks: FieldProjection[]) => void
}) {
  const [filter, setFilter] = useState('')
  const [customPath, setCustomPath] = useState('')
  const [customCount, setCustomCount] = useState(false)
  const options: { projection: FieldProjection; origin?: MetricField['origin'] }[] = [
    ...fields.flatMap((field) => [
      { projection: { path: field.path }, origin: field.origin },
      ...(field.countable ? [{ projection: { path: field.path, transform: 'length' as const }, origin: field.origin }] : []),
    ]),
    // A typed path (or a saved one no longer offered) stays visible and untickable.
    ...picks.filter((pick) => !fields.some((field) => field.path === pick.path)).map((projection) => ({ projection })),
  ]
  const needle = filter.trim().toLocaleLowerCase()
  const visible = needle ? options.filter((option) => option.projection.path.toLocaleLowerCase().includes(needle)) : options
  function toggle(projection: FieldProjection, checked: boolean) {
    if (!checked) onChange(picks.filter((pick) => !sameProjection(pick, projection)))
    else onChange(multi ? [...picks, projection] : [projection])
  }
  function addCustom() {
    const path = customPath.trim()
    if (!path) return
    toggle(customCount ? { path, transform: 'length' } : { path }, true)
    setCustomPath('')
    setCustomCount(false)
  }

  return <fieldset className="space-y-2">
    <legend className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">Field</legend>
    <label className="flex cursor-pointer items-center gap-2 rounded-md border px-2.5 py-1.5 text-sm hover:bg-muted/40">
      <Checkbox aria-label="Whole output" checked={picks.length === 0} onCheckedChange={(checked) => { if (checked === true) onChange([]) }} />
      Whole output
    </label>
    {options.length > 8 && <Input aria-label="Filter fields" placeholder="Filter fields…" value={filter} onChange={(event) => setFilter(event.target.value)} />}
    {visible.length > 0 && <div role="group" aria-label="Output fields" className="max-h-64 space-y-0.5 overflow-y-auto rounded-md border p-1">
      {visible.map(({ projection, origin }) => {
        const label = projectionLabel(projection)
        return <label key={label} className="flex cursor-pointer items-center gap-2 rounded px-1.5 py-1 text-xs hover:bg-muted/40">
          <Checkbox aria-label={label} checked={picks.some((pick) => sameProjection(pick, projection))} onCheckedChange={(checked) => toggle(projection, checked === true)} />
          <span className="min-w-0 flex-1 truncate font-mono">{label}</span>
          {origin === 'declared' && <span className="text-[10px] text-muted-foreground">Output Parser</span>}
        </label>
      })}
    </div>}
    {status && <p role="status" className="text-xs text-muted-foreground">{status}</p>}
    <div className="flex flex-wrap items-end gap-2">
      <div className="min-w-40 flex-1 space-y-1">
        <Label htmlFor="metric-field-custom-path" className="text-xs">Field path</Label>
        <Input id="metric-field-custom-path" className="font-mono" placeholder="e.g. test_metrics.roc_auc" value={customPath} onChange={(event) => setCustomPath(event.target.value)} onKeyDown={(event) => { if (event.key === 'Enter') { event.preventDefault(); addCustom() } }} />
      </div>
      <label className="flex items-center gap-1.5 pb-2 text-xs"><Checkbox aria-label="Count items" checked={customCount} onCheckedChange={(checked) => setCustomCount(checked === true)} />Count items</label>
      <Button type="button" variant="outline" size="sm" disabled={!customPath.trim()} onClick={addCustom}>Add path</Button>
    </div>
  </fieldset>
}
