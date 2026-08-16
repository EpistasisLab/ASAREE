import { BrainCircuit } from 'lucide-react'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { hashToChartHue } from '@/lib/utils'
import { EditableNodeTitle } from './EditableNodeTitle'
import { NodeInspectorDialog } from './NodeInspectorDialog'
import type { MemoryNodeConfig, MemoryNodeData, ProtocolNode } from '@/types/protocols'

const ACCENT = hashToChartHue('memory')

// Deliberately minimal -- there's nothing real to configure yet. Connecting
// this node into an agent declares intent for a future phase (porting
// agentic-core's existing episodic-memory service into ASAREE's execution
// path), but has NO effect on execution today; see MemoryNodeData's own
// comment in types/protocols.ts.
export function MemoryNodeInspector({
  node,
  onChange,
  onDelete,
  onClose,
}: {
  node: (ProtocolNode & { data: MemoryNodeData }) | null
  onChange: (nodeId: string, data: MemoryNodeData) => void
  onDelete: (nodeId: string) => void
  onClose: () => void
}) {
  if (!node) return null
  const data = node.data
  const config = data.config

  function patchConfig(patch: Partial<MemoryNodeConfig>) {
    onChange(node!.id, { ...data, config: { ...config, ...patch } })
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
      <div className="space-y-1.5">
        <Label htmlFor="memory-name">Name</Label>
        <Input id="memory-name" value={config.name} onChange={(e) => patchConfig({ name: e.target.value })} />
      </div>
    </NodeInspectorDialog>
  )
}
