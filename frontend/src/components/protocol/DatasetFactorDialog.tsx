import { useState } from 'react'
import { ArrowDown, ArrowUp, Split } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedDatasets, reconcileDatasetFactor, datasetFactorConflict, datasetFactorModes as modes, DATASET_FACTOR_PATH, type DatasetFactorMode as Mode } from '@/lib/datasetFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { computeFactorName } from './factorLevels'

export function DatasetFactorDialog({ datasetNodeId, graph, factors, initialMode, initialAgentId, onClose, onSave, onRemove }: {
  datasetNodeId: string
  graph: ProtocolGraph
  factors: DesignFactor[]
  initialMode?: Mode
  initialAgentId?: string
  onClose: () => void
  onSave: (factor: DesignFactor, ownerId: string, previousName?: string) => Promise<void>
  onRemove: (name: string) => Promise<void>
}) {
  const dataset = graph.nodes.find((node) => node.id === datasetNodeId)!
  const agents = graph.nodes.filter((node) => ['agent', 'sub_agent'].includes(node.type) && graph.edges.some((edge) => edge.source === datasetNodeId && edge.target === node.id && ['dataset', 'resource', 'tool'].includes(edge.targetHandle ?? '')))
  const [agentId, setAgentId] = useState(initialAgentId ?? agents.find((agent) => agent.data.factor_bindings?.[DATASET_FACTOR_PATH])?.id ?? agents[0]?.id ?? '')
  const existingFor = (ownerId: string, path: string) => factors.find((factor) => factor.name === graph.nodes.find((node) => node.id === ownerId)?.data.factor_bindings?.[path])
  const legacy = existingFor(datasetNodeId, 'config')
  const initialFactor = existingFor(agentId, DATASET_FACTOR_PATH) ?? existingFor(datasetNodeId, 'config.enabled')
  const initial = initialFactor && initialFactor.level_type !== 'boolean' && initialFactor.level_type ? reconcileDatasetFactor(initialFactor, graph, agentId) : initialFactor
  const [mode, setMode] = useState<Mode>(initialMode ?? (initial?.level_type as Mode) ?? 'boolean')
  function seed(nextMode: Mode, nextAgentId: string): DesignFactor {
    const ownerId = nextMode === 'boolean' ? datasetNodeId : nextAgentId
    const existing = existingFor(ownerId, nextMode === 'boolean' ? 'config.enabled' : DATASET_FACTOR_PATH)
    if (existing?.level_type === nextMode || (nextMode === 'boolean' && existing && !existing.level_type)) return {
      ...existing,
      level_type: nextMode,
      level_labels: existing.level_labels ?? existing.levels.map((level) => level ? 'Enabled' : 'Disabled'),
    }
    const owner = graph.nodes.find((node) => node.id === ownerId)
    const factor: DesignFactor = {
      name: existing?.name ?? computeFactorName(String(owner?.data.label || 'Dataset'), nextMode === 'boolean' ? 'Enabled' : 'Datasets', factors.map((factor) => factor.name)),
      level_type: nextMode,
      levels: nextMode === 'boolean' ? [false, true] : [],
      level_labels: nextMode === 'boolean' ? ['Disabled', 'Enabled'] : undefined,
    }
    return nextMode === 'boolean' ? factor : reconcileDatasetFactor(factor, graph, ownerId)
  }
  const [draft, setDraft] = useState<DesignFactor>(() => {
    const factor = initialMode ? seed(mode, agentId) : initial ?? seed(mode, agentId)
    return { ...factor, level_type: factor.level_type ?? 'boolean', level_labels: factor.level_labels ?? factor.levels.map((level) => level ? 'Enabled' : 'Disabled') }
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const ownerId = mode === 'boolean' ? datasetNodeId : agentId
  const current = existingFor(ownerId, mode === 'boolean' ? 'config.enabled' : DATASET_FACTOR_PATH)
  const affected = mode === 'boolean' ? [dataset] : connectedDatasets(graph, agentId)
  const unavailable = (value: Mode) => value !== 'boolean' && !agentId
    ? `${modes[value]} is unavailable because this Dataset is not connected to an Agent. Connect this Dataset to an Agent first.`
    : datasetFactorConflict(graph, factors, datasetNodeId, value, agentId)
  const blockedReason = unavailable(mode)
  const labels = draft.level_labels ?? []
  function change(nextMode: Mode, nextAgentId = agentId) {
    setAgentId(nextAgentId)
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
      setError(error instanceof Error ? error.message : 'Could not save the dataset factor.')
    } finally { setSaving(false) }
  }
  const valid = draft.name.trim() && ownerId && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length
  return <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
    <DialogContent className="sm:max-w-xl">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2"><Split className="size-5 text-chart-2" />Dataset factor</DialogTitle>
        <DialogDescription>Choose how datasets vary across experimental cells.</DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        {agents.length > 1 && <div className="space-y-2"><Label>Agent</Label><Select value={agentId} disabled={saving} onValueChange={(id) => {
          if (!id) return
          const existing = existingFor(id, DATASET_FACTOR_PATH)
          change((existing?.level_type as Mode) ?? mode, id)
        }}><SelectTrigger className="w-full"><SelectValue>{String(agents.find((agent) => agent.id === agentId)?.data.label)}</SelectValue></SelectTrigger><SelectContent>{agents.map((agent) => <SelectItem key={agent.id} value={agent.id}>{String(agent.data.label)}</SelectItem>)}</SelectContent></Select></div>}
        <div className="space-y-2"><Label>Factor type</Label><div className="flex flex-wrap gap-2">{(Object.keys(modes) as Mode[]).map((value) => <Button key={value} size="sm" variant={mode === value ? 'default' : unavailable(value) ? 'secondary' : 'outline'} aria-pressed={mode === value} className={unavailable(value) ? 'border-dashed border-border' : undefined} title={unavailable(value)} disabled={saving} onClick={() => change(value)}>{modes[value]}</Button>)}</div></div>
        {legacy && <div className="space-y-2 rounded-md border p-3"><p className="text-xs text-muted-foreground">This Dataset has an existing configuration factor: {legacy.name}. Remove it before creating a Dataset factor.</p><Button variant="destructive" size="sm" disabled={saving} onClick={async () => {
          setSaving(true)
          try { await onRemove(legacy.name) } catch (error) { setError(error instanceof Error ? error.message : 'Could not remove the dataset factor.') } finally { setSaving(false) }
        }}>Remove configuration factor</Button></div>}
        {blockedReason ? <p role="status" className="rounded-md border p-3 text-sm text-muted-foreground">{blockedReason}</p> : <>
        <p className="text-xs text-muted-foreground">{mode === 'boolean' ? 'Enable or disable this Dataset node.' : mode === 'dataset_selection' ? 'Each cell receives exactly one connected dataset.' : 'Each cell enables every connected dataset, including deactivated datasets, or disables them all.'}</p>
        <div className="space-y-2"><Label htmlFor="dataset-factor-name">Factor name</Label><Input id="dataset-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
        {mode !== 'boolean' && affected.length < (mode === 'dataset_toggle' ? 1 : 2) && <p className="text-xs text-destructive">Connect at least {mode === 'dataset_toggle' ? 'one dataset' : 'two different datasets'} before generating cells.</p>}
        <div className="max-h-72 space-y-2 overflow-y-auto">{draft.levels.map((level, index) => <div key={index} className="flex items-center gap-2 rounded-md border p-2">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="font-mono text-xs">{mode === 'boolean' ? (level ? 'Enabled' : 'Disabled') : mode === 'dataset_toggle' ? ((level as string[]).length ? 'All enabled' : 'All disabled') : String(affected.find((node) => (node.data.config as { dataset_id?: string })?.dataset_id === (level as string[])[0])?.data.label ?? 'Unavailable dataset')}{index === 0 && mode !== 'boolean' ? ' · default test selection' : ''}</p>
            <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} disabled={saving} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
          </div>
          {mode !== 'boolean' && <><Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button></>}
        </div>)}</div>
        <p className="text-xs text-muted-foreground">Agent-level factors follow current Dataset connections. Changes require design review and regeneration.</p>
        {current && <Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button>}
        </>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button><Button disabled={saving || !valid || !!blockedReason} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
