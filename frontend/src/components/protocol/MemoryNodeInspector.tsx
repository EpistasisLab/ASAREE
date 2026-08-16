import { BrainCircuit } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { FactorBindableField } from './FactorBindableField'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { MemoryNodeConfig, MemoryNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('memory')

// Deliberately minimal -- there's nothing real to configure yet. Connecting
// this node into an agent declares intent for a future phase (porting
// agentic-core's existing episodic-memory service into ASAREE's execution
// path), but has NO effect on execution today; see MemoryNodeData's own
// comment in types/protocols.ts. The Enabled factor below ships for the
// same reason -- declared capability only, no runtime effect yet.
export function MemoryNodeInspector({
  node,
  experimentId,
  factorNodeLabel,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: MemoryNodeData }) | null
  experimentId: string | null
  // The agent-traced display label (see bindableFields.ts's
  // agentTracedLabel) -- distinct from data.label, which is this node's own
  // plain label shown in the header title.
  factorNodeLabel: string
  onChange: (nodeId: string, data: MemoryNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config
  const bindings = data.factor_bindings ?? {}

  function patchConfig(patch: Partial<MemoryNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
  }

  function bindFactor(fieldPath: string, factorName: string) {
    onChange(node!.id, { ...data, factor_bindings: { ...bindings, [fieldPath]: factorName } })
  }

  function unbindFactor(fieldPath: string) {
    const next = { ...bindings }
    delete next[fieldPath]
    onChange(node!.id, { ...data, factor_bindings: next })
  }

  return (
    <NodeInspectorDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      accent={ACCENT}
      title={
        <>
          <BrainCircuit className="size-5" style={{ color: ACCENT }} />
          <EditableNodeTitle label={data.label} placeholder="Memory" onCommit={(label) => onChange(node.id, { ...data, label })} />
        </>
      }
      onDelete={() => onDelete(node.id)}
      onClose={onClose}
    >
      <div className="rounded-lg border border-dashed px-3 py-2 text-xs text-muted-foreground">
        Not yet wired up to real memory storage -- connecting this to an agent declares intent for a future phase,
        but has no effect on execution yet.
      </div>
      <FactorBindableField
        experimentId={experimentId}
        fieldPath="config.enabled"
        defaultLabel="Enabled"
        nodeLabel={factorNodeLabel}
        levelType="boolean"
        boundFactorName={bindings['config.enabled']}
        onBind={(name) => bindFactor('config.enabled', name)}
        onUnbind={() => unbindFactor('config.enabled')}
      >
        <div className="flex w-full items-center justify-between rounded-lg border px-3 py-2">
          <Label htmlFor="memory-enabled">Enabled</Label>
          <Switch id="memory-enabled" checked={config.enabled ?? true} onCheckedChange={(checked) => patchConfig({ enabled: checked })} />
        </div>
      </FactorBindableField>
      <div className="space-y-1.5">
        <Label htmlFor="memory-name">Name</Label>
        <Input id="memory-name" value={config.name} onChange={(e) => patchConfig({ name: e.target.value })} />
      </div>
    </NodeInspectorDialog>
  )
}
