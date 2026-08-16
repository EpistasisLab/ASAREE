import type { NodeProps } from '@xyflow/react'
import { Database } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { DatasetNodeData } from '@/types/protocols'
import { hasBoundFactor } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Declares which registered dataset an Agent's workspace tools operate on --
// a real runtime effect once wired (see DatasetNodeData's own comment in
// types/protocols.ts), unlike Memory's own "declares intent, no effect yet"
// status, so NOT dashed. Rendered as n8n's own small circle-with-icon (see
// CircleNode), same as every other connector source. Wires into the Agent's
// shared Tool connector (handleId="tool", matching CONNECTOR_PANEL_INFO.tool
// in ProtocolCanvas.tsx and _NODE_TYPE_TO_HANDLE in protocol_execution.py),
// not a dedicated "dataset" handle -- see AgentNode.tsx's own comment.
const ACCENT = hashToChartHue('dataset')

export function DatasetNode({ id, data, selected }: NodeProps & { data: DatasetNodeData }) {
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
      hasFactor={hasBoundFactor(data)}
    />
  )
}
