import { factorScopeLabel, factorRecipients } from '@/lib/sharedFactors'
import { useState } from 'react'
import { ArrowDown, ArrowUp, Split } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { connectedScripts, reconcileScriptFactor, requiredScriptIssues, scriptFactorConflict, scriptFactorModes as modes, SCRIPT_FACTOR_PATH, isIndividualScriptMode, scriptFactorPath, type ScriptFactorMode as Mode } from '@/lib/scriptFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { computeFactorName } from './factorLevels'

export function ScriptFactorDialog({ scriptNodeId, graph, factors, initialMode, onClose, onSave, onRemove }: {
  scriptNodeId: string
  graph: ProtocolGraph
  factors: DesignFactor[]
  initialMode?: Mode
  onClose: () => void
  onSave: (factor: DesignFactor, ownerId: string, previousName?: string) => Promise<void>
  onRemove: (name: string) => Promise<void>
}) {
  const script = graph.nodes.find((node) => node.id === scriptNodeId)!
  const agents = factorRecipients(graph, scriptNodeId, 'script')
  const agentId = agents.find((agent) => agent.data.factor_bindings?.[SCRIPT_FACTOR_PATH])?.id ?? agents[0]?.id ?? ''
  const existingFor = (ownerId: string, path: string) => factors.find((factor) => factor.name === graph.nodes.find((node) => node.id === ownerId)?.data.factor_bindings?.[path])
  const initialFactor = existingFor(agentId, SCRIPT_FACTOR_PATH) ?? existingFor(scriptNodeId, 'config.enabled')
  const initial = initialFactor && !isIndividualScriptMode(initialFactor.level_type as Mode) && initialFactor.level_type ? reconcileScriptFactor(initialFactor, graph, agentId) : initialFactor
  const [mode, setMode] = useState<Mode>(initialMode ?? (initial?.level_type as Mode) ?? 'boolean')
  function seed(nextMode: Mode, nextAgentId: string): DesignFactor {
    const ownerId = isIndividualScriptMode(nextMode) ? scriptNodeId : nextAgentId
    const existing = existingFor(ownerId, scriptFactorPath(nextMode))
    if (existing?.level_type === nextMode || (nextMode === 'boolean' && existing && !existing.level_type)) return {
      ...existing,
      level_type: nextMode,
      level_labels: existing.level_labels ?? existing.levels.map((level, index) => nextMode === 'boolean' ? level ? 'Enabled' : 'Disabled' : `Level ${index + 1}`),
    }
    const owner = graph.nodes.find((node) => node.id === ownerId)
    const factor: DesignFactor = {
      name: existing?.name ?? computeFactorName(String(owner?.data.label || 'Script'), nextMode === 'boolean' ? 'Enabled' : 'Scripts', factors.map((factor) => factor.name)),
      level_type: nextMode,
      levels: nextMode === 'boolean' ? [false, true] : [],
      level_labels: nextMode === 'boolean' ? ['Disabled', 'Enabled'] : undefined,
    }
    return isIndividualScriptMode(nextMode) ? factor : reconcileScriptFactor(factor, graph, ownerId)
  }
  const [draft, setDraft] = useState<DesignFactor>(() => {
    const factor = initialMode ? seed(mode, agentId) : initial ?? seed(mode, agentId)
    return { ...factor, level_type: factor.level_type ?? 'boolean', level_labels: factor.level_labels ?? factor.levels.map((level, index) => mode === 'boolean' ? level ? 'Enabled' : 'Disabled' : `Level ${index + 1}`) }
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const ownerId = isIndividualScriptMode(mode) ? scriptNodeId : agentId
  const current = existingFor(ownerId, scriptFactorPath(mode))
  const affected = isIndividualScriptMode(mode) ? [script] : connectedScripts(graph, agentId)
  const unavailable = (value: Mode) => !isIndividualScriptMode(value) && !agentId
    ? `${modes[value]} is unavailable because this Script is not connected to an Agent. Connect this Script to an Agent first.`
    : scriptFactorConflict(graph, factors, scriptNodeId, value, agentId)
  const previewGraph = { ...graph, nodes: graph.nodes.map((node) => node.id === ownerId ? { ...node, data: { ...node.data, factor_bindings: { ...node.data.factor_bindings, [scriptFactorPath(mode)]: draft.name } } } : node) }
  const requiredIssues = requiredScriptIssues(previewGraph, [...factors.filter((factor) => factor.name !== current?.name), draft])
  const blockedReason = unavailable(mode)
  const labels = draft.level_labels ?? []
  function change(nextMode: Mode, nextAgentId = agentId) {
    setMode(nextMode)
    setDraft(seed(nextMode, nextAgentId))
    setError('')
  }
  function move(index: number, offset: number) {
    const levels = [...draft.levels]
    const nextLabels = [...labels]
    ;[levels[index], levels[index + offset]] = [levels[index + offset], levels[index]]
    ;[nextLabels[index], nextLabels[index + offset]] = [nextLabels[index + offset], nextLabels[index]]
    setDraft({ ...draft, levels, level_labels: nextLabels })
  }
  async function commit(remove = false) {
    if (!remove && blockedReason) return
    setSaving(true)
    setError('')
    try {
      if (remove && current) await onRemove(current.name)
      else {
        await onSave({ ...draft, name: draft.name.trim(), level_labels: labels.map((label) => label.trim()) }, ownerId, current?.name)
        onClose()
      }
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not save the script factor.')
    } finally { setSaving(false) }
  }
  const valid = draft.name.trim() && ownerId && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length
  return <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
    <DialogContent className="sm:max-w-xl">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2"><Split className="size-5 text-chart-2" />Script factor</DialogTitle>
        <DialogDescription>Choose how scripts vary across experimental cells.</DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        {agents.length > 0 && <p className="text-xs text-muted-foreground">Applies to: {factorScopeLabel(graph, scriptNodeId, 'script')}</p>}
        <div className="space-y-2"><Label>Factor type</Label><div className="flex flex-wrap gap-2">{(Object.keys(modes) as Mode[]).map((value) => <Button key={value} size="sm" variant={mode === value ? 'default' : unavailable(value) ? 'secondary' : 'outline'} aria-pressed={mode === value} className={unavailable(value) ? 'border-dashed border-border' : undefined} title={unavailable(value)} disabled={saving} onClick={() => change(value)}>{modes[value]}</Button>)}</div></div>
        {blockedReason ? <p role="status" className="rounded-md border p-3 text-sm text-muted-foreground">{blockedReason}</p> : <>
        <p className="text-xs text-muted-foreground">{mode === 'boolean' ? 'Enable or disable this Script node.' : mode === 'script_selection' ? 'Each cell receives exactly one connected script.' : 'Each cell enables every connected script, including deactivated scripts, or disables them all.'}</p>
        <div className="space-y-2"><Label htmlFor="script-factor-name">Factor name</Label><Input id="script-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
        {!isIndividualScriptMode(mode) && affected.length < (mode === 'script_toggle' ? 1 : 2) && <p className="text-xs text-destructive">Connect at least {mode === 'script_toggle' ? 'one script' : 'two different scripts'} before generating cells.</p>}
        <div className="max-h-72 space-y-2 overflow-y-auto">{draft.levels.map((level, index) => <div key={index} className="flex items-center gap-2 rounded-md border p-2">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="font-mono text-xs">{mode === 'boolean' ? (level ? 'Enabled' : 'Disabled') : mode === 'script_toggle' ? ((level as string[]).length ? 'All enabled' : 'All disabled') : String(affected.find((node) => node.id === (level as string[])[0])?.data.label ?? 'Unavailable script')}{index === 0 && mode !== 'boolean' ? ' · default test selection' : ''}</p>
            <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} disabled={saving} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
          </div>
          {mode !== 'boolean' && <><Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button></>}
        </div>)}</div>
        <p className="text-xs text-muted-foreground">Shared factors require matching Script connections. Changes require design review and regeneration.</p>
        {requiredIssues.map((issue) => <p key={issue} role="status" className="text-xs text-destructive">{issue}</p>)}
        </>}
        {current && <Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button><Button disabled={saving || !valid || !!blockedReason || requiredIssues.length > 0} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
