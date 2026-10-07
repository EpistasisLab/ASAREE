import { useState } from 'react'
import { ArrowDown, ArrowUp, Split } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { connectedKnowledge, knowledgeId, reconcileKnowledgeFactor, knowledgeFactorConflict, knowledgeFactorModes as modes, KNOWLEDGE_FACTOR_PATH, type KnowledgeFactorMode as Mode } from '@/lib/knowledgeFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { computeFactorName } from './factorLevels'

export function KnowledgeFactorDialog({ knowledgeNodeId, graph, factors, initialMode, initialAgentId, onClose, onSave, onRemove }: {
  knowledgeNodeId: string
  graph: ProtocolGraph
  factors: DesignFactor[]
  initialMode?: Mode
  initialAgentId?: string
  onClose: () => void
  onSave: (factor: DesignFactor, ownerId: string, previousName?: string) => Promise<void>
  onRemove: (name: string) => Promise<void>
}) {
  const knowledge = graph.nodes.find((node) => node.id === knowledgeNodeId)!
  const agents = graph.nodes.filter((node) => ['agent', 'sub_agent'].includes(node.type) && graph.edges.some((edge) => edge.source === knowledgeNodeId && edge.target === node.id && edge.targetHandle === 'knowledge'))
  const [agentId, setAgentId] = useState(initialAgentId ?? agents.find((agent) => agent.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH])?.id ?? agents[0]?.id ?? '')
  const existingFor = (ownerId: string, path: string) => factors.find((factor) => factor.name === graph.nodes.find((node) => node.id === ownerId)?.data.factor_bindings?.[path])
  const initialFactor = existingFor(agentId, KNOWLEDGE_FACTOR_PATH) ?? existingFor(knowledgeNodeId, 'config.enabled')
  const initial = initialFactor && initialFactor.level_type !== 'boolean' && initialFactor.level_type ? reconcileKnowledgeFactor(initialFactor, graph, agentId) : initialFactor
  const [mode, setMode] = useState<Mode>(initialMode ?? (initial?.level_type as Mode) ?? 'boolean')
  function seed(nextMode: Mode, nextAgentId: string): DesignFactor {
    const ownerId = nextMode === 'boolean' ? knowledgeNodeId : nextAgentId
    const existing = existingFor(ownerId, nextMode === 'boolean' ? 'config.enabled' : KNOWLEDGE_FACTOR_PATH)
    if (existing?.level_type === nextMode || (nextMode === 'boolean' && existing && !existing.level_type)) return {
      ...existing,
      level_type: nextMode,
      level_labels: existing.level_labels ?? existing.levels.map((level) => level ? 'Enabled' : 'Disabled'),
    }
    const owner = graph.nodes.find((node) => node.id === ownerId)
    const factor: DesignFactor = {
      name: existing?.name ?? computeFactorName(String(owner?.data.label || 'Knowledge'), nextMode === 'boolean' ? 'Enabled' : 'Knowledge', factors.map((factor) => factor.name)),
      level_type: nextMode,
      levels: nextMode === 'boolean' ? [false, true] : [],
      level_labels: nextMode === 'boolean' ? ['Disabled', 'Enabled'] : undefined,
    }
    return nextMode === 'boolean' ? factor : reconcileKnowledgeFactor(factor, graph, ownerId)
  }
  const [draft, setDraft] = useState<DesignFactor>(() => {
    const factor = initialMode ? seed(mode, agentId) : initial ?? seed(mode, agentId)
    return { ...factor, level_type: factor.level_type ?? 'boolean', level_labels: factor.level_labels ?? factor.levels.map((level) => level ? 'Enabled' : 'Disabled') }
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const ownerId = mode === 'boolean' ? knowledgeNodeId : agentId
  const current = existingFor(ownerId, mode === 'boolean' ? 'config.enabled' : KNOWLEDGE_FACTOR_PATH)
  const affected = mode === 'boolean' ? [knowledge] : connectedKnowledge(graph, agentId)
  const unavailable = (value: Mode) => value !== 'boolean' && !agentId
    ? `${modes[value]} is unavailable because this Knowledge is not connected to an Agent. Connect this Knowledge to an Agent first.`
    : knowledgeFactorConflict(graph, factors, knowledgeNodeId, value, agentId)
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
      else await onSave({ ...draft, name: draft.name.trim(), level_labels: labels.map((label) => label.trim()) }, ownerId, current?.name)
      onClose()
    } catch (error) {
      setError(error instanceof Error ? error.message : 'Could not save the knowledge factor.')
    } finally { setSaving(false) }
  }
  const valid = draft.name.trim() && ownerId && labels.length === draft.levels.length && labels.every((label) => label.trim()) && new Set(labels.map((label) => label.trim())).size === labels.length
  return <Dialog open onOpenChange={(open) => !open && !saving && onClose()}>
    <DialogContent className="sm:max-w-xl">
      <DialogHeader>
        <DialogTitle className="flex items-center gap-2"><Split className="size-5 text-chart-2" />Knowledge factor</DialogTitle>
        <DialogDescription>Choose how knowledge varies across experimental cells.</DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        {agents.length > 1 && <div className="space-y-2"><Label>Agent</Label><Select value={agentId} disabled={saving} onValueChange={(id) => {
          if (!id) return
          const existing = existingFor(id, KNOWLEDGE_FACTOR_PATH)
          change((existing?.level_type as Mode) ?? mode, id)
        }}><SelectTrigger className="w-full"><SelectValue>{String(agents.find((agent) => agent.id === agentId)?.data.label)}</SelectValue></SelectTrigger><SelectContent>{agents.map((agent) => <SelectItem key={agent.id} value={agent.id}>{String(agent.data.label)}</SelectItem>)}</SelectContent></Select></div>}
        <div className="space-y-2"><Label>Factor type</Label><div className="flex flex-wrap gap-2">{(Object.keys(modes) as Mode[]).map((value) => <Button key={value} size="sm" variant={mode === value ? 'default' : unavailable(value) ? 'secondary' : 'outline'} aria-pressed={mode === value} className={unavailable(value) ? 'border-dashed border-border' : undefined} title={unavailable(value)} disabled={saving} onClick={() => change(value)}>{modes[value]}</Button>)}</div></div>
        {blockedReason ? <p role="status" className="rounded-md border p-3 text-sm text-muted-foreground">{blockedReason}</p> : <>
        <p className="text-xs text-muted-foreground">{mode === 'boolean' ? 'Enable or disable this Knowledge node.' : mode === 'knowledge_selection' ? 'Each cell receives exactly one connected knowledge source.' : 'Each cell enables every connected knowledge source, including deactivated sources, or disables them all.'}</p>
        <div className="space-y-2"><Label htmlFor="knowledge-factor-name">Factor name</Label><Input id="knowledge-factor-name" value={draft.name} disabled={saving} onChange={(event) => setDraft({ ...draft, name: event.target.value })} /></div>
        {mode !== 'boolean' && affected.length < (mode === 'knowledge_toggle' ? 1 : 2) && <p className="text-xs text-destructive">Connect at least {mode === 'knowledge_toggle' ? 'one knowledge source' : 'two different knowledge sources'} before generating cells.</p>}
        <div className="max-h-72 space-y-2 overflow-y-auto">{draft.levels.map((level, index) => <div key={index} className="flex items-center gap-2 rounded-md border p-2">
          <div className="min-w-0 flex-1 space-y-1">
            <p className="font-mono text-xs">{mode === 'boolean' ? (level ? 'Enabled' : 'Disabled') : mode === 'knowledge_toggle' ? ((level as string[]).length ? 'All enabled' : 'All disabled') : String(affected.find((node) => knowledgeId(node) === (level as string[])[0])?.data.label ?? 'Unavailable knowledge')}{index === 0 && mode !== 'boolean' ? ' · default test selection' : ''}</p>
            <Input aria-label={`Level ${index + 1} label`} value={labels[index] ?? ''} disabled={saving} onChange={(event) => setDraft({ ...draft, level_labels: labels.map((label, i) => i === index ? event.target.value : label) })} />
          </div>
          {mode !== 'boolean' && <><Button variant="ghost" size="icon-sm" aria-label="Move level up" disabled={saving || index === 0} onClick={() => move(index, -1)}><ArrowUp className="size-3.5" /></Button><Button variant="ghost" size="icon-sm" aria-label="Move level down" disabled={saving || index === draft.levels.length - 1} onClick={() => move(index, 1)}><ArrowDown className="size-3.5" /></Button></>}
        </div>)}</div>
        <p className="text-xs text-muted-foreground">Agent-level factors follow current Knowledge connections. Changes require design review and regeneration.</p>
        {current && <Button variant="destructive" size="sm" disabled={saving} onClick={() => void commit(true)}>Remove factor</Button>}
        </>}
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      </div>
      <DialogFooter><Button variant="outline" disabled={saving} onClick={onClose}>Cancel</Button><Button disabled={saving || !valid || !!blockedReason} onClick={() => void commit()}>{saving ? 'Saving…' : 'Save factor'}</Button></DialogFooter>
    </DialogContent>
  </Dialog>
}
