import { nodeAccent } from '@/lib/nodeAccent'
import { useReactFlow, type NodeProps } from '@xyflow/react'
import { BrainCircuit } from 'lucide-react'
import type { MemoryNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Visual/validation scaffolding only -- see MemoryNodeData's own comment in
// types/protocols.ts. Rendered as a small circle-with-icon (see
// CircleNode); the dashed ring is the "not yet functional" signal here
// (permanently inert, not a missing-field warning), so no separate warning
// badge on top of it. config.enabled still gets the same dimmed/hover-
// toolbar treatment as every other connector despite having no backend
// effect yet -- consistent canvas behavior for the field today, ready to
// mean something the moment Memory execution actually lands.
const ACCENT = nodeAccent('memory')

export function MemoryNode({ id, data, selected }: NodeProps & { data: MemoryNodeData }) {
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={BrainCircuit}
      label={data.label}
      placeholder="Memory"
      handleId="memory"
      dashed
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
