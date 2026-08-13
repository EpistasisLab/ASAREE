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
      className={`group relative w-36 rounded-md border bg-card px-2 py-1.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
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
      <Handle
        type="target"
        position={Position.Top}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <div className="flex items-center gap-1.5">
        <Bot className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        <EditableNodeLabel nodeId={id} label={data.label} placeholder="Agent" renaming={renaming} onRenamingChange={setRenaming} />
      </div>
      <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={data.config?.goal || undefined}>
        {data.config?.goal || 'No goal set'}
      </p>
      {/* n8n's own 3 bottom sub-connectors (Chat Model/Memory/Tool), adapted:
          required LLM (exactly one), optional repeatable Tool, optional
          max-1 Memory (visual scaffolding only -- see MemoryNodeData). The
          main pipeline's own downstream handle moves to the right slot so
          all 4 bottom dots stay visually distinct. */}
      <Handle
        type="target"
        id="llm"
        position={Position.Bottom}
        style={{ left: '15%' }}
        title="LLM (required)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="15%">LLM</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="llm" left="15%" />
      <Handle
        type="target"
        id="tool"
        position={Position.Bottom}
        style={{ left: '38%' }}
        title="Tool"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="38%">Tool</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="tool" left="38%" allowMultiple />
      <Handle
        type="target"
        id="memory"
        position={Position.Bottom}
        style={{ left: '61%' }}
        title="Memory (not yet functional)"
        className="!size-2 !border-2 !border-dashed !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="61%">Memory</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="memory" left="61%" />
      <Handle
        type="source"
        position={Position.Bottom}
        style={{ left: '87%' }}
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
    </div>
  )
}
