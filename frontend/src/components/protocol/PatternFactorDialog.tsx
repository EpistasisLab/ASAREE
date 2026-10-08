import { factorRecipients, factorScopeLabel } from '@/lib/sharedFactors'
import { useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Split, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { factorValueKey } from '@/lib/experiment'
import { PatternLevelRow } from './FactorEditorDialog'
import { computeFactorName, defaultFactorLevelLabels, seedLevels } from './factorLevels'
import { PATTERN_FACTOR_PATH, patternFactorFields, patternFactorConflict } from './patternFactors'

export function PatternFactorDialog({ patternNodeId, graph, nodeLabel, factors, initialFieldPath, onClose, onSave, onRemove }: {
  patternNodeId: string
  graph: ProtocolGraph
  nodeLabel: string
  factors: DesignFactor[]
  initialFieldPath?: string
  onClose: () => void
  onSave: (factor: DesignFactor, fieldPath: string, ownerId: string, previousName?: string) => Promise<unknown>
  onRemove: (name: string, fieldPath: string, ownerId: string) => Promise<unknown>
}) {
  const agents = factorRecipients(graph, patternNodeId, 'pattern')
  const agentId = agents.find((node) => node.data.factor_bindings?.[PATTERN_FACTOR_PATH])?.id ?? agents[0]?.id ?? ''
  const fields = patternFactorFields(patternNodeId, graph.nodes, graph.edges, agentId)
    .sort((a, b) => Number(b.fieldPath === PATTERN_FACTOR_PATH) - Number(a.fieldPath === PATTERN_FACTOR_PATH) || a.label.localeCompare(b.label))
  const fieldFor = (path: string) => fields.find((field) => field.fieldPath === path)!
  const existingFor = (path: string) => factors.find((factor) => factor.name === graph.nodes.find((node) => node.id === fieldFor(path).nodeId)?.data.factor_bindings?.[path])
  const [path, setPath] = useState(initialFieldPath ?? fields.find((field) => existingFor(field.fieldPath))?.fieldPath ?? PATTERN_FACTOR_PATH)
  function seed(path: string, ownerId = agentId): DesignFactor {
    const field = patternFactorFields(patternNodeId, graph.nodes, graph.edges, ownerId).find((field) => field.fieldPath === path)!
    const existing = factors.find((factor) => factor.name === graph.nodes.find((node) => node.id === field.nodeId)?.data.factor_bindings?.[path])
    if (existing) return { ...existing, level_labels: existing.level_labels ?? defaultFactorLevelLabels(existing.name, existing.levels.length, existing.level_type) }
    const levels = field.levelType === 'pattern' ? [field.currentValue, field.alternateValue] : field.levelType === 'boolean' ? [false, true] : seedLevels(field.currentValue)
    const name = computeFactorName(nodeLabel, field.label, factors.map((factor) => factor.name))
    return { name, level_type: field.levelType, levels, level_labels: defaultFactorLevelLabels(name, levels.length, field.levelType) }
  }
  const [draft, setDraft] = useState(() => seed(path))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const current = existingFor(path)
  const blocked = (path: string) => patternFactorConflict(patternNodeId, path, graph.nodes, graph.edges, agentId)
  const labels = draft.level_labels ?? []
  const numeric = draft.level_type === 'number'
  const structured = draft.level_type === 'pattern'
  const boolean = draft.level_type === 'boolean'
  const keys = draft.levels.map((level) => numeric && !String(level).trim() ? undefined : factorValueKey(numeric ? Number(level) : level))
  const duplicates = keys.flatMap((key, index) => key !== undefined && keys.some((other, i) => i !== index && key === other) ? [index + 1] : [])
  const validLevel = (level: unknown) => {
    if (boolean) return typeof level === 'boolean'
    if (!structured) return !!String(level).trim() && (!numeric || Number.isInteger(Number(level)) && Number(level) >= 1) && (path !== 'config.observation_format' || ['raw', 'summarized'].includes(String(level)))
    const value = level as Record<string, unknown>
    const slug = String(value.execution_pattern)
    const params = (value.pattern_params as Record<string, Record<string, unknown>>)?.[slug]
    return ['reason_act', 'single_agent_baseline'].includes(slug) && params && Number.isInteger(params.max_iterations) && Number(params.max_iterations) >= 1 && (slug !== 'reason_act' || !params.include_scratchpad || Number.isInteger(params.scratchpad_window) && Number(params.scratchpad_window) >= 1)
  }
  const valid = draft.name.trim() && draft.levels.length >= 2 && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length && draft.levels.every(validLevel) && !duplicates.length
  function move(index: number, offset: number) {
    const levels = [...draft.levels], nextLabels = [...labels]
    ;[levels[index], levels[index + offset]] = [levels[index + offset], levels[index]]
    ;[nextLabels[index], nextLabels[index + offset]] = [nextLabels[index + offset], nextLabels[index]]
    setDraft({ ...draft, levels, level_labels: nextLabels })
  }
  async function commit(remove = false) {
    if (!remove && (!valid || blocked(path))) return
    setSaving(true)
    setError('')
    try {
      if (remove && current) await onRemove(current.name, path, fieldFor(path).nodeId)
      else {
        await onSave({ ...draft, name: draft.name.trim(), levels: numeric ? draft.levels.map(Number) : draft.levels, level_labels: labels.map((label) => label.trim()) }, path, fieldFor(path).nodeId, current?.name)
        onClose()
      }
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not save the pattern factor.') }
    finally { setSaving(false) }
  }
  return <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
    <DialogContent className="sm:max-w-xl">
      <DialogHeader><DialogTitle className="flex items-center gap-2"><Split className="size-5 text-chart-2" />Pattern factor</DialogTitle><DialogDescription>Choose how patterns vary across experimental cells.</DialogDescription></DialogHeader>
      <div className="space-y-4">
        {agents.length > 0 && <p className="text-xs text-muted-foreground">Applies to: {factorScopeLabel(graph, patternNodeId, 'pattern')}</p>}
        <div className="space-y-2"><Label>Factor type</Label><div className="flex flex-wrap gap-2">{fields.map((field) => <Button key={field.fieldPath} size="sm" variant={path === field.fieldPath ? 'default' : blocked(field.fieldPath) ? 'secondary' : 'outline'} aria-pressed={path === field.fieldPath} disabled={saving} onClick={() => { setPath(field.fieldPath); setDraft(seed(field.fieldPath)); setError('') }}>{field.label}</Button>)}</div></div>
        {blocked(path) ? <p role="status" className="rounded-md border p-3 text-sm text-muted-foreground">{blocked(path)}</p> : <>
          <p className="text-xs text-primary">{structured ? 'Compare complete pattern configurations. Use individual parameter factors to measure their separate effects.' : 'Vary this parameter while keeping the other pattern settings.'}</p>
          <div className="space-y-2"><Label htmlFor="pattern-factor-name">Factor name</Label><Input id="pattern-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
          <fieldset disabled={saving} className="max-h-80 space-y-2 overflow-y-auto">{draft.levels.map((level, index) => <div key={index} className="flex items-start gap-2 rounded-md border p-2">
            <div className="min-w-0 flex-1 space-y-2">
              <p className="font-mono text-xs">Level {index + 1}{index === 0 ? ' · default test selection' : ''}</p>
              <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
              {structured ? <PatternLevelRow value={level as Record<string, unknown>} onChange={(next) => setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? next : value) })} /> : boolean ? <p className="font-mono text-xs">{level ? 'Enabled' : 'Disabled'}</p> : path === 'config.observation_format' ? <Select value={String(level) || '__none__'} onValueChange={(value) => value && setDraft({ ...draft, levels: draft.levels.map((level, i) => i === index ? value : level) })}><SelectTrigger aria-label={`Level ${index + 1} value`} className="w-full"><SelectValue>{String(level) || 'Select format…'}</SelectValue></SelectTrigger><SelectContent><SelectItem value="__none__" disabled>Select format…</SelectItem>{['raw', 'summarized'].map((format) => <SelectItem key={format} value={format}>{format}</SelectItem>)}</SelectContent></Select> : <Input aria-label={`Level ${index + 1} value`} type={numeric ? 'number' : 'text'} min={numeric ? 1 : undefined} step={numeric ? 1 : undefined} value={String(level)} onChange={(event) => setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? event.target.value : value) })} />}
            </div>
            {!boolean && <><Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Remove level" disabled={saving || draft.levels.length <= 2} onClick={() => setDraft({ ...draft, levels: draft.levels.filter((_, i) => i !== index), level_labels: labels.filter((_, i) => i !== index) })}><X className="size-3.5" /></Button></>}
          </div>)}</fieldset>
          {duplicates.length > 0 && <p role="alert" className="text-xs text-destructive">Duplicate levels: {duplicates.join(', ')}. Change or remove a duplicate before saving.</p>}
          {!boolean && <Button variant="outline" size="sm" disabled={saving} onClick={() => setDraft({ ...draft, levels: [...draft.levels, structured ? fieldFor(path).alternateValue : ''], level_labels: [...labels, `level${labels.length + 1}`] })}><Plus className="size-3.5" />Add level</Button>}
          <p className="text-xs text-muted-foreground">Changes require design review and regeneration.</p>
        </>}
        {current && <Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button><Button disabled={saving || !valid || !!blocked(path)} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
