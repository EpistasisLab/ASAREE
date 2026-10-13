import { useState } from 'react'
import { ArrowDown, ArrowUp, Plus, Split, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { factorValueKey } from '@/lib/experiment'
import type { PromptReferenceScope } from '@/lib/promptReferences'
import type { DesignFactor } from '@/types/experiments'
import type { AgentNodeData, ProtocolNode } from '@/types/protocols'
import { agentFactorFields } from './bindableFields'
import { defaultSystemPrompt } from './defaultSystemPrompt'
import { computeFactorName, defaultFactorLevelLabels, seedLevels } from './factorLevels'
import { PromptReferenceField } from './PromptReferenceField'

export function AgentFactorDialog({ node, factors, initialFieldPath, referenceScope, onClose, onSave, onRemove }: {
  node: ProtocolNode & { data: AgentNodeData }
  factors: DesignFactor[]
  initialFieldPath?: string
  referenceScope: PromptReferenceScope
  onClose: () => void
  onSave: (factor: DesignFactor, fieldPath: string, previousName?: string) => Promise<unknown>
  onRemove: (name: string, fieldPath: string) => Promise<unknown>
}) {
  const bindings = node.data.factor_bindings ?? {}
  const existingFor = (path: string) => factors.find((factor) => factor.name === bindings[path])
  const [path, setPath] = useState(initialFieldPath ?? agentFactorFields.find((field) => existingFor(field.fieldPath))?.fieldPath ?? 'active')
  function seed(fieldPath: string): DesignFactor {
    const existing = existingFor(fieldPath)
    const boolean = fieldPath === 'active'
    const type = boolean ? 'boolean' : 'text'
    if (existing) return { ...existing, level_type: type, level_labels: existing.level_labels ?? (boolean ? existing.levels.map((level) => level ? 'Enabled' : 'Disabled') : defaultFactorLevelLabels(existing.name, existing.levels.length, type)) }
    const label = boolean ? 'Enabled' : fieldPath === 'config.prompt' ? 'Prompt' : 'System prompt'
    const name = computeFactorName(node.data.label || 'Agent', label, factors.map((factor) => factor.name))
    const value = fieldPath === 'config.prompt' ? node.data.config.prompt : node.data.config.system_prompt
    return { name, level_type: type, levels: boolean ? [false, true] : seedLevels(value), level_labels: boolean ? ['Disabled', 'Enabled'] : defaultFactorLevelLabels(name, 2, type) }
  }
  const [draft, setDraft] = useState(() => seed(path))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const boolean = path === 'active'
  const current = existingFor(path)
  const labels = draft.level_labels ?? []
  const duplicateLevels = new Set(draft.levels.map(factorValueKey)).size !== draft.levels.length
  const valid = draft.name.trim() && draft.levels.length >= 2 && !duplicateLevels && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length
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
        await onSave({ ...draft, name: draft.name.trim(), level_labels: labels.map((label) => label.trim()) }, path, current?.name)
        onClose()
      }
    } catch (error) { setError(error instanceof Error ? error.message : 'Could not save the agent factor.') }
    finally { setSaving(false) }
  }
  return <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
    <DialogContent className="sm:max-w-2xl">
      <DialogHeader><DialogTitle className="flex items-center gap-2"><Split className="size-5 text-chart-2" />Agent factor</DialogTitle><DialogDescription>Choose how this agent varies across experimental cells.</DialogDescription></DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2"><Label>Factor type</Label><div className="flex flex-wrap gap-2">{agentFactorFields.map((field) => <Button key={field.fieldPath} size="sm" variant={path === field.fieldPath ? 'default' : 'outline'} aria-pressed={path === field.fieldPath} disabled={saving} onClick={() => { setPath(field.fieldPath); setDraft(seed(field.fieldPath)); setError('') }}>{field.label}</Button>)}</div></div>
        <p className="text-xs text-muted-foreground">{boolean ? 'Enable or disable this Agent node. Disabled sequential agents pass upstream output through. Cells must keep an executable agent and every required lead, supervisor, or gated worker active.' : 'Replace this field for each cell while keeping this agent’s other settings and connections. An empty level uses the normal default for this field.'}</p>
        <div className="space-y-2"><Label htmlFor="agent-factor-name">Factor name</Label><Input id="agent-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
        <fieldset disabled={saving} className="max-h-96 space-y-2 overflow-y-auto">{draft.levels.map((level, index) => <div key={`${path}:${index}`} className="flex items-start gap-2 rounded-md border p-2">
          <div className="min-w-0 flex-1 space-y-2">
            <p className="font-mono text-xs">{boolean ? level ? 'Enabled' : 'Disabled' : `Level ${index + 1}${index === 0 ? ' · default test selection' : ''}`}</p>
            <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
            {!boolean && <PromptReferenceField id={`agent-factor-level-${index}`} label={`Level ${index + 1} ${path === 'config.prompt' ? 'prompt' : 'system prompt'}`} value={String(level)} scope={referenceScope} placeholder={path === 'config.system_prompt' ? defaultSystemPrompt(node.data.label, 'Agent') : node.data.config.goal} className="font-mono text-xs" onChange={(next) => setDraft({ ...draft, levels: draft.levels.map((value, i) => i === index ? next : value) })} />}
          </div>
          {!boolean && <><Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Remove level" disabled={saving || draft.levels.length <= 2} onClick={() => setDraft({ ...draft, levels: draft.levels.filter((_, i) => i !== index), level_labels: labels.filter((_, i) => i !== index) })}><X className="size-3.5" /></Button></>}
        </div>)}</fieldset>
        {duplicateLevels && <p role="alert" className="text-xs text-destructive">Each level must have a different value. Change or remove a duplicate before saving.</p>}
        {!boolean && <Button variant="outline" size="sm" disabled={saving} onClick={() => setDraft({ ...draft, levels: [...draft.levels, ''], level_labels: [...labels, `level${labels.length + 1}`] })}><Plus className="size-3.5" />Add level</Button>}
        <p className="text-xs text-muted-foreground">Changes require design review and regeneration.</p>
        {current && <Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button><Button disabled={saving || !valid} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
