import { nodeAccent } from '@/lib/nodeAccent'
import { useReactFlow, type NodeProps } from '@xyflow/react'
import { Braces } from 'lucide-react'
import type { OutputParserNodeData } from '@/types/protocols'
import { boundFactorCount } from '../bindableFields'
import { CircleNode } from './CircleNode'

// The field spec that turns an Agent's prose answer into a typed payload --
// see OutputParserNodeData's own comment in types/protocols.ts for why this is
// a node rather than the agent field it used to be.
//
// Not dashed: unlike Memory, this is fully wired up and has a real (and
// billable) runtime effect the moment it's connected. `enabled` is the way to
// switch that cost off for a run without losing the field spec, and it dims
// the node the same way every other switchable connector does.
const ACCENT = nodeAccent('output_parser')

export function OutputParserNode({ id, data, selected }: NodeProps & { data: OutputParserNodeData }) {
  const { updateNodeData } = useReactFlow()
  const enabled = data.config?.enabled ?? true
  // Same presence check as nodeConfigIssues.ts's own output_parser case, and
  // the same wording -- a contract with no named field appends nothing to the
  // prompt and extracts nothing, so the node is inert rather than
  // misconfigured in some subtler way.
  const named = (data.config?.output_contract?.fields ?? []).filter((field) => field.name.trim()).length

  return (
    <CircleNode
      id={id}
      selected={selected}
      accent={ACCENT}
      icon={Braces}
      label={data.label}
      placeholder="Output Parser"
      handleId="output_parser"
      warning={named === 0 ? 'No fields declared' : undefined}
      factorCount={boundFactorCount(data)}
      dimmed={!enabled}
      isActive={enabled}
      onToggleActive={() => updateNodeData(id, { config: { ...data.config, enabled: !enabled } })}
    />
  )
}
