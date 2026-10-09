import type { ModelCapabilities } from '@/types/llmSettings'
import { modelCapabilities } from '@/lib/modelCapabilities'
import { useEffect, useRef, useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Split, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import type { DesignFactor } from '@/types/experiments'
import { factorValueKey } from '@/lib/experiment'
import type { ProtocolNode } from '@/types/protocols'
import { bindableFieldsForNode } from './bindableFields'
import { LlmConfigLevelRow } from './FactorEditorDialog'
import { ModelField } from './ModelField'
import { useProviderModels } from './useProviderModels'
import { computeFactorName, defaultFactorLevelLabels, seedLevels } from './factorLevels'

export function ModelFactorDialog({ node, nodeLabel, factors, initialFieldPath, initialLevelIndex, onClose, onSave, onRemove }: {
  node: ProtocolNode
  nodeLabel: string
  factors: DesignFactor[]
  initialFieldPath?: string
  initialLevelIndex?: number
  onClose: () => void
  onSave: (factor: DesignFactor, fieldPath: string, previousName?: string) => Promise<unknown>
  onRemove: (name: string, fieldPath: string) => Promise<unknown>
}) {
  const config = { ...node.data.config } as Record<string, unknown>
  const { models, modelsQuery, capabilities: resolvedCapabilities, capabilitiesPending, capabilitiesError } = useProviderModels(config.provider as string | undefined, config.model as string | undefined, config.resolved_capabilities as ModelCapabilities | undefined)
  const modelInfo = models.find((model) => model.id === config.model)
  const capabilities = modelCapabilities(resolvedCapabilities ?? modelInfo, config.resolved_capabilities as ModelCapabilities | undefined)
  const showTemperature = !capabilitiesPending && capabilities.supports_temperature
  const showEffort = !capabilitiesPending && capabilities.supports_effort
  const effortLevels = capabilities.effort_levels
  const fields = bindableFieldsForNode(node)
  const available = (fieldPath: string) => fieldPath === 'config.temperature' ? showTemperature : fieldPath === 'config.effort' ? showEffort : true
  const visibleFields = fields.filter((field) => available(field.fieldPath) || node.data.factor_bindings?.[field.fieldPath] || initialFieldPath === field.fieldPath)
    .sort((a, b) => Number(b.fieldPath === 'config') - Number(a.fieldPath === 'config') || a.label.localeCompare(b.label))
  const bindings = node.data.factor_bindings ?? {}
  const [path, setPath] = useState(initialFieldPath ?? Object.keys(bindings)[0] ?? 'config')
  const existingFor = (fieldPath: string) => factors.find((factor) => factor.name === bindings[fieldPath])
  function seed(fieldPath: string): DesignFactor {
    const existing = existingFor(fieldPath)
    if (existing) return { ...existing, level_labels: existing.level_labels ?? defaultFactorLevelLabels(existing.name, existing.levels.length, existing.level_type) }
    const field = fields.find((field) => field.fieldPath === fieldPath)!
    const value = fieldPath === 'config' ? config : config[fieldPath.slice(7)]
    const levels = field.levelType === 'model_config' ? [value, { ...config, model: '' }] : seedLevels(value)
    const name = computeFactorName(nodeLabel, field.label, factors.map((factor) => factor.name))
    return { name, level_type: field.levelType, levels, level_labels: defaultFactorLevelLabels(name, levels.length, field.levelType) }
  }
  const [draft, setDraft] = useState(() => seed(path))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const initialLevelRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (initialLevelIndex == null) return
    initialLevelRef.current?.scrollIntoView?.({ block: 'nearest' })
  }, [initialLevelIndex])
  const current = existingFor(path)
  const blocked = (fieldPath: string) => fieldPath === 'config'
    ? Object.keys(bindings).some((key) => key.startsWith('config.'))
    : !!bindings.config
  const labels = draft.level_labels ?? []
  const numeric = draft.level_type === 'number'
  const structured = draft.level_type === 'model_config'
  const levelKeys = draft.levels.map((level) => numeric && !String(level).trim() ? undefined : factorValueKey(numeric ? Number(level) : level))
  const duplicateLevels = draft.levels.flatMap((level, index) => {
    const configured = structured ? !!(level as Record<string, unknown>).model : !!String(level).trim()
    return configured && levelKeys.some((key, other) => other !== index && key === levelKeys[index]) ? [index + 1] : []
  })
  const valid = draft.name.trim() && draft.levels.length >= 2 && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length && draft.levels.every((level) => structured ? !!(level as Record<string, unknown>).model : String(level).trim() && (!numeric || Number.isFinite(Number(level)))) && duplicateLevels.length === 0
  function move(index: number, offset: number) {
    const levels = [...draft.levels], nextLabels = [...labels]
    ;[levels[index], levels[index + offset]] = [levels[index + offset], levels[index]]
    ;[nextLabels[index], nextLabels[index + offset]] = [nextLabels[index + offset], nextLabels[index]]
    setDraft({ ...draft, levels, level_labels: nextLabels })
  }
  async function commit(remove = false) {
    setSaving(true)
    setError('')
    try {
      if (remove && current) await onRemove(current.name, path)
      else {
        await onSave({ ...draft, name: draft.name.trim(), levels: numeric ? draft.levels.map(Number) : draft.levels, level_labels: labels.map((label) => label.trim()) }, path, current?.name)
        onClose()
      }
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not save the model factor.') }
    finally { setSaving(false) }
  }
  return <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
    <DialogContent className="sm:max-w-xl" initialFocus={initialLevelIndex == null ? undefined : () => initialLevelRef.current?.querySelector<HTMLInputElement>('input') ?? true}>
      <DialogHeader><DialogTitle className="flex items-center gap-2"><Split className="size-5 text-chart-2" />Model factor</DialogTitle><DialogDescription>Choose how models vary across experimental cells.</DialogDescription></DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2"><Label>Factor type</Label><div className="flex flex-wrap gap-2">{visibleFields.map((field) => <Button key={field.fieldPath} size="sm" variant={path === field.fieldPath ? 'default' : blocked(field.fieldPath) ? 'secondary' : 'outline'} aria-pressed={path === field.fieldPath} disabled={saving} onClick={() => { setPath(field.fieldPath); setDraft(seed(field.fieldPath)); setError('') }}>{field.label}</Button>)}</div></div>
        {blocked(path) ? <p role="status" className="rounded-md border p-3 text-sm text-muted-foreground">Model levels cannot be combined with individual configuration factors. Select the existing factor type and remove its binding first.</p> : <>
          <p className="text-xs text-primary">{structured ? 'Compare complete model configurations. Use individual parameter factors to measure their separate effects.' : 'Vary this configuration field while keeping the other model settings.'}</p>
          {!available(path) && <p role="status" className="text-xs text-muted-foreground">The selected model does not support this field. Remove its factor or choose a model that supports it.</p>}
          <div className="space-y-2"><Label htmlFor="model-factor-name">Factor name</Label><Input id="model-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
          <fieldset disabled={saving} className="max-h-80 space-y-2 overflow-y-auto">{draft.levels.map((level, index) => <div key={index} ref={index === initialLevelIndex ? initialLevelRef : undefined} className={`flex items-start gap-2 rounded-md border p-2 ${index === initialLevelIndex ? 'border-chart-2' : ''}`}>
            <div className="min-w-0 flex-1 space-y-2">
              <p className="font-mono text-xs">Level {index + 1}{index === 0 ? ' · default test selection' : ''}</p>
              <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
              {structured ? <LlmConfigLevelRow value={level as Record<string, unknown>} onChange={(next) => setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? next : value) })} /> : path === 'config.model' ? <>
                <Label htmlFor={`model-factor-level-${index}`}>Level {index + 1} model</Label>
                <ModelField id={`model-factor-level-${index}`} value={String(level)} models={models} isLoading={modelsQuery.isLoading} disabledModelIds={draft.levels.filter((_, i) => i !== index).map(String)} onChange={(next) => setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? next : value) })} />
              </> : path === 'config.effort' ? <>
                <Label htmlFor={`model-factor-level-${index}`}>Level {index + 1} effort</Label>
                <Select value={String(level) || '__none__'} onValueChange={(next) => next && next !== '__none__' && !draft.levels.some((value, i) => i !== index && value === next) && setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? next : value) })}>
                  <SelectTrigger id={`model-factor-level-${index}`} className="w-full"><SelectValue>{() => String(level) || 'Select effort…'}</SelectValue></SelectTrigger>
                  <SelectContent><SelectItem value="__none__" disabled>Select effort…</SelectItem>{effortLevels.map((effort) => <SelectItem key={effort} value={effort} disabled={effort !== level && draft.levels.some((value, i) => i !== index && value === effort)}>{effort}</SelectItem>)}</SelectContent>
                </Select>
              </> : <Input aria-label={`Level ${index + 1} value`} type={numeric ? 'number' : 'text'} step="any" value={String(level)} onChange={(event) => setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? event.target.value : value) })} />}
            </div>
            <Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button>
            <Button variant="ghost" size="icon-sm" aria-label="Remove level" disabled={saving || draft.levels.length <= 2} onClick={() => setDraft({ ...draft, levels: draft.levels.filter((_, i) => i !== index), level_labels: labels.filter((_, i) => i !== index) })}><X className="size-3.5" /></Button>
          </div>)}</fieldset>
          {duplicateLevels.length > 0 && <p role="alert" className="text-xs text-destructive">Duplicate levels: {duplicateLevels.join(', ')}. {structured ? 'Each level must use a different model configuration.' : 'Each level must have a different value.'} Change or remove a duplicate before saving.</p>}
          <Button variant="outline" size="sm" disabled={saving} onClick={() => setDraft({ ...draft, levels: [...draft.levels, structured ? { ...node.data.config, model: '' } : ''], level_labels: [...labels, `level${labels.length + 1}`] })}><Plus className="size-3.5" />Add level</Button>
          <p className="text-xs text-muted-foreground">Changes require design review and regeneration.</p>
          {current && <Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button>}
        </>}
        {capabilitiesPending && <p role="status" className="text-xs text-muted-foreground">{capabilitiesError ?? 'Resolving model capabilities…'}</p>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button><Button disabled={saving || !valid || blocked(path) || !available(path)} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
