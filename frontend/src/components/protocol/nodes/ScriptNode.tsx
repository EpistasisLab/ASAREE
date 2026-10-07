import { nodeAccent } from '@/lib/nodeAccent'
import { useReactFlow, type NodeProps } from '@xyflow/react'
import { Code2 } from 'lucide-react'
import type { ScriptNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { CircleNode } from './CircleNode'

// Carries Python source staged for execution through run_wired_script, or
// supplied as a Tool Step argument. Availability factors control its presence.
// Rendered as a small circle-with-icon (see CircleNode), same as
// every other connector source. Wires into the Agent's shared Tool
// connector (handleId="tool", alongside mcp_tool -- see AgentNode.tsx's own
// comment), not a dedicated "script" handle.
const ACCENT = nodeAccent('script')

export function ScriptNode({ id, data, selected }: NodeProps & { data: ScriptNodeData }) {
  const { updateNodeData } = useReactFlow()
  const { requestScriptFactor, experimentLocked } = useProtocolCanvasActions()
  const controlled = !!data.scriptFactorControlled
  const enabled = controlled || (data.config?.enabled ?? true)
  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Code2}
      label={data.label}
      placeholder="Script"
      handleId="tool"
      warning={data.config?.code ? undefined : 'No code set'}
      factorCount={controlled ? 1 : boundFactorCount(data)}
      onMakeFactor={!experimentLocked && requestScriptFactor ? () => requestScriptFactor(id) : undefined}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={controlled ? undefined : () => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
