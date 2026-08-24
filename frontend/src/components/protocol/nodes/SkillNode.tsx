import { useReactFlow, type NodeProps } from '@xyflow/react'
import { ScrollText } from 'lucide-react'
import { hashToChartHue } from '@/lib/utils'
import type { SkillNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// Names one registered Agent Skill for the Agent it's wired into -- a real
// runtime effect once wired (see SkillNodeData in types/protocols.ts), like
// Dataset and unlike Memory, so NOT dashed. Wires into the Agent's Skill
// connector (handleId="skill", matching CONNECTOR_PANEL_INFO.skill in
// ProtocolCanvas.tsx and _NODE_TYPE_TO_HANDLE in protocol_execution.py),
// which sits on the agent's top edge -- hence handlePosition="bottom", same
// as Dataset and the pattern nodes.
const ACCENT = hashToChartHue('skill')

export function SkillNode({ id, data, selected }: NodeProps & { data: SkillNodeData }) {
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={ScrollText}
      label={data.label}
      placeholder="Skill"
      handleId="skill"
      handlePosition="bottom"
      warning={data.config?.skill_id ? undefined : 'No skill selected'}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
