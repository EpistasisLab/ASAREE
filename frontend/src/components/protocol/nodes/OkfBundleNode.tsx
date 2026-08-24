import { useReactFlow, type NodeProps } from '@xyflow/react'
import { BookMarked } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { OkfBundleNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Names one registered OKF bundle -- a directory of Markdown concepts on the
// server that the wired Agent reads and writes during a run. A real runtime
// effect (its MCP server's tools join the agent's allow-list, see
// _resolve_knowledge_config), so NOT dashed. Wires into the Agent's Knowledge
// connector (handleId="knowledge", matching CONNECTOR_PANEL_INFO.knowledge in
// ProtocolCanvas.tsx and _NODE_TYPE_TO_HANDLE in protocol_execution.py), which
// sits on the agent's top edge -- hence handlePosition="bottom", same as Skill.
const ACCENT = hashToChartHue('okf_bundle')

export function OkfBundleNode({ id, data, selected }: NodeProps & { data: OkfBundleNodeData }) {
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true
  // server_name, not bundle_id, decides whether this node does anything: it's
  // the field the run namespaces tools against, so a node without it silently
  // contributes nothing.
  const warning = !data.config?.server_name
    ? 'No bundle selected'
    : (data.config?.tool_names?.length ?? 0) === 0
      ? 'No tools discovered -- the bundle server may have failed to start'
      : undefined

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={BookMarked}
      label={data.label}
      placeholder="OKF Bundle"
      handleId="knowledge"
      handlePosition="bottom"
      warning={warning}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
