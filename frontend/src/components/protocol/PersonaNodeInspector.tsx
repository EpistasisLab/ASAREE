import { Power, Split, User } from 'lucide-react'
import { nodeAccent } from '@/lib/nodeAccent'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { EditableNodeTitle } from './EditableNodeTitle'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { ProtocolNode, PersonaNodeData } from '@/types/protocols'

const ACCENT = nodeAccent('persona')

export function PersonaNodeInspector({ node, onChange, onDelete, onClose, connectorFactorName }: {
  node: (ProtocolNode & { data: PersonaNodeData }) | null
  onChange: (nodeId: string, data: PersonaNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
  connectorFactorName?: string
}) {
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}
  function unbind(path: string) {
    const next = { ...bindings }; delete next[path]
    onChange(node!.id, { ...data, factor_bindings: next })
  }
  return <NodeInspectorDialog open onOpenChange={open => !open && onClose()} accent={ACCENT}
    title={<><User className="size-5" style={{ color: ACCENT }} /><EditableNodeTitle label={data.label} placeholder="Persona" onCommit={label => onChange(node.id, { ...data, label })} /></>}
    onDelete={() => onDelete(node.id)} onClose={onClose}>
    <div className="space-y-4">
      <p className="rounded-lg border px-3 py-2 text-xs text-muted-foreground"><span className="font-medium text-chart-2">Make factor</span> is in the node toolbar. Hover over the Persona node and click <Split className="inline size-3 text-chart-2" aria-label="Make factor" />. Use <Power className="inline size-3" aria-label="Power" /> to activate or deactivate it.</p>
      <div className="space-y-2 rounded-lg border px-3 py-2">
        <div className="flex items-center justify-between"><Label htmlFor="persona-enabled">Enabled</Label><Switch id="persona-enabled" checked={!!connectorFactorName || (config.enabled ?? true)} disabled={!!connectorFactorName || !!bindings.config} onCheckedChange={enabled => onChange(node.id, { ...data, config: { ...config, enabled } })} /></div>
        <p className="text-xs text-muted-foreground">{connectorFactorName ? `Availability is controlled by connector factor ${connectorFactorName}. Remove it to restore individual controls.` : 'Off: the wired agent does not receive these persona instructions.'}</p>
        {bindings['config.enabled'] && <p className="text-xs text-chart-2">Factor: {bindings['config.enabled']}</p>}
        {bindings.config && <div className="space-y-2"><p className="text-xs text-muted-foreground">Legacy persona factor: {bindings.config}. Remove this binding before using the new factor modes. Its declaration remains in Design until removed or rebound.</p><Button variant="outline" size="sm" onClick={() => unbind('config')}>Remove legacy binding</Button></div>}
        {connectorFactorName && Object.entries(bindings).filter(([path]) => path !== 'config').map(([path, name]) => <div key={path} className="space-y-1"><p className="text-xs text-destructive">Individual factor {name} conflicts with this connector.</p><Button variant="outline" size="sm" onClick={() => unbind(path)}>Remove individual binding</Button></div>)}
      </div>
      <div className="space-y-2"><Label>Persona</Label><p className="text-sm font-medium">{config.persona_label || data.label}</p>{config.persona_description && <p className="text-xs text-muted-foreground">{config.persona_description}</p>}{config.persona_file && <p className="font-mono text-xs text-muted-foreground">{config.persona_file}</p>}<pre className="max-h-[calc(100vh-24rem)] min-h-32 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-xs whitespace-pre-wrap">{config.persona_text || '(empty — unmodified system prompt)'}</pre></div>
      <p className="text-xs text-muted-foreground">Instructions are prepended to the agent’s system prompt. Edit or upload personas in the Personas drawer, then add a new node to use them. Existing nodes and published experiments keep their saved text.</p>
    </div>
  </NodeInspectorDialog>
}
