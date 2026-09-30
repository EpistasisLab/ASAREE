import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { Cog } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent } from '@/lib/utils'
import { nodeAccent } from '@/lib/nodeAccent'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { NodeRunStatus, ToolStepNodeData } from '@/types/protocols'
import { boundFactorCount, hasBoundFactor } from '../bindableFields'
import { connectorLefts } from '../layout'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { ConnectorAddStub } from './ConnectorAddStub'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { NodeFactorBadge } from './NodeFactorBadge'
import { NodeHoverToolbar } from './NodeHoverToolbar'

const ACCENT = nodeAccent('tool_step')
const CONNECTOR_LEFT = connectorLefts('tool_step')

// Same compact w-36 card as CriticGateNode: a Tool Step is a main-flow step
// with one connector (Tool -- one MCP Tool, optionally one Script), not an
// Agent-sized host.
export function ToolStepNode({
  id,
  data,
  selected,
}: NodeProps & { data: ToolStepNodeData & { runStatus?: NodeRunStatus; runTruncated?: boolean } }) {
  const isActive = data.active ?? true
  const toolName = data.config?.tool_name
  const summary = !isActive ? 'Step disabled' : toolName ? `${toolName}()` : 'No tool selected'
  const badge = nodeRunBadge(data.runStatus, data.runTruncated)
  const { updateNodeData } = useReactFlow()
  const { requestMakeFactor } = useProtocolCanvasActions()

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${isActive ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar
        nodeId={id}
        isActive={isActive}
        onToggleActive={() => updateNodeData(id, { active: !isActive })}
        onMakeFactor={() => requestMakeFactor(id)}
      />
      {badge && (
        <Badge className={`absolute -top-2.5 ${hasBoundFactor(data) ? 'right-6' : 'right-1.5'} ${badge.className}`}>
          {badge.label}
        </Badge>
      )}
      {hasBoundFactor(data) && <NodeFactorBadge count={boundFactorCount(data)} className="-top-3 -right-3" />}
      <Handle
        type="target"
        position={Position.Left}
        title="Input (the upstream node whose payload this step sends)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Cog className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'Tool Step'}
        </span>
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={summary}>
        {summary}
      </p>
      <Handle
        type="target"
        id="tool"
        position={Position.Bottom}
        style={{ left: CONNECTOR_LEFT.tool }}
        title="Tool (one MCP Tool, optionally one Script)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.tool}>Tool</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="tool" left={CONNECTOR_LEFT.tool} alwaysVisible />
      <Handle
        type="source"
        position={Position.Right}
        title="Output (the tool's parsed result)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
