import { useReactFlow, type NodeProps } from '@xyflow/react'
import { Database } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { DatasetNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Declares which registered dataset an Agent's workspace tools operate on --
// a real runtime effect once wired (see DatasetNodeData's own comment in
// types/protocols.ts), unlike Memory's own "declares intent, no effect yet"
// status, so NOT dashed. Rendered as a small circle-with-icon (see
// CircleNode), same as every other connector source. Wires into the Agent's
// shared Tool connector (handleId="tool", matching CONNECTOR_PANEL_INFO.tool
// in ProtocolCanvas.tsx and _NODE_TYPE_TO_HANDLE in protocol_execution.py),
// not a dedicated "dataset" handle -- see AgentNode.tsx's own comment. The
// hover toolbar's own power icon toggles this same config.enabled
// _build_user_input already checks before emitting a "Dataset context"
// block, same as the Switch in its own inspector.
const ACCENT = hashToChartHue('dataset')

export function DatasetNode({ id, data, selected }: NodeProps & { data: DatasetNodeData }) {
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Database}
      label={data.label}
      placeholder="Dataset"
      handleId="tool"
      warning={data.config?.dataset_id ? undefined : 'No dataset selected'}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
