import { useState } from 'react'
import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { AgentNodeData, NodeRunStatus } from '@/types/protocols'
import { ConnectorAddStub } from './ConnectorAddStub'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { EditableNodeLabel } from './EditableNodeLabel'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { NodeSummaryLine } from './NodeSummaryLine'

// All "agent" nodes share one hue in V1 -- type-based coloring (CLAUDE.md's
// hash-driven tint rule for "real category variety"), not per-instance
// variety, since a node's identity is its type, not its label.
const ACCENT = hashToChartHue('agent')

export function AgentNode({ id, data, selected }: NodeProps & { data: AgentNodeData & { runStatus?: NodeRunStatus } }) {
  const badge = nodeRunBadge(data.runStatus)
  const [renaming, setRenaming] = useState(false)
  const { updateNodeData } = useReactFlow()
  const isActive = data.active ?? true

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-60 rounded-md border bg-card px-2.5 py-3 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${isActive ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar
        nodeId={id}
        isActive={isActive}
        onToggleActive={() => updateNodeData(id, { active: !isActive })}
        onRename={() => setRenaming(true)}
      />
      {badge && (
        <Badge className={`absolute -top-2.5 right-1.5 ${badge.className}`}>{badge.label}</Badge>
      )}
      {/* Main pipeline flow is left-to-right (n8n's own convention) --
          input on the left, output on the right. The 4 sub-connectors
          below stay on the bottom edge regardless, same as n8n's own AI
          sub-connectors (Chat Model/Memory/Tool) always hang below a node
          no matter which way the main flow runs. */}
      <Handle
        type="target"
        position={Position.Left}
        title="Input (the previous pipeline step's output)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Bot className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder="Agent" renaming={renaming} onRenamingChange={setRenaming} />
      </div>
      <NodeSummaryLine text={data.config?.goal || null} emptyLabel="No goal set" />
      {/* n8n's own 3 bottom sub-connectors (Chat Model/Memory/Tool), adapted
          and extended with a 4th (Architectural Pattern, ASAREE's own
          addition, no n8n equivalent): required LLM (exactly one), optional
          max-1 Memory (visual scaffolding only -- see MemoryNodeData),
          optional repeatable Architectural Pattern (same unlimited cardinality
          as Tool -- an agent can combine several patterns at once) and Tool. */}
      <Handle
        type="target"
        id="llm"
        position={Position.Bottom}
        style={{ left: '14%' }}
        title="LLM (required)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="14%">LLM</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="llm" left="14%" />
      <Handle
        type="target"
        id="architectural_pattern"
        position={Position.Bottom}
        style={{ left: '38%' }}
        title="Architectural Pattern (not yet functional)"
        className="!size-2 !border-2 !border-dashed !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="38%">Pattern</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="architectural_pattern" left="38%" allowMultiple />
      <Handle
        type="target"
        id="memory"
        position={Position.Bottom}
        style={{ left: '62%' }}
        title="Memory (not yet functional)"
        className="!size-2 !border-2 !border-dashed !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="62%">Memory</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="memory" left="62%" />
      <Handle
        type="target"
        id="tool"
        position={Position.Bottom}
        style={{ left: '86%' }}
        title="Tool"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="86%">Tool</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="tool" left="86%" allowMultiple />
      <Handle
        type="source"
        position={Position.Right}
        title="Output (drag to the next pipeline step)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
