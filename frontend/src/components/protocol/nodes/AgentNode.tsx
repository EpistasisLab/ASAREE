import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { AgentNodeData, NodeRunStatus } from '@/types/protocols'
import { ConnectorAddStub } from './ConnectorAddStub'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { MainEdgeAddStub } from './MainEdgeAddStub'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { NodeSummaryLine } from './NodeSummaryLine'

// All "agent" nodes share one hue in V1 -- type-based coloring (CLAUDE.md's
// hash-driven tint rule for "real category variety"), not per-instance
// variety, since a node's identity is its type, not its label.
const ACCENT = hashToChartHue('agent')

export function AgentNode({
  id,
  data,
  selected,
}: NodeProps & { data: AgentNodeData & { runStatus?: NodeRunStatus; missingLlm?: boolean } }) {
  const badge = nodeRunBadge(data.runStatus)
  const { updateNodeData } = useReactFlow()
  const isActive = data.active ?? true

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative w-60 rounded-md border bg-card px-2.5 py-3 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${isActive ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar nodeId={id} isActive={isActive} onToggleActive={() => updateNodeData(id, { active: !isActive })} />
      {badge && (
        <Badge className={`absolute -top-2.5 right-1.5 ${badge.className}`}>{badge.label}</Badge>
      )}
      {/* Main flow is left-to-right (n8n's own convention) -- the 4
          sub-connectors below stay on the bottom edge regardless, same as
          n8n's own AI sub-connectors (Chat Model/Memory/Tool) always hang
          below a node no matter which way the main flow runs.

          Left/right no longer mean strict sequential handoff -- they mean
          "this agent can interact with that one" (which coordination
          strategy is active decides what "interact" actually does at
          runtime, see design_spec.coordination_strategy). Fan-out/fan-in
          are both unrestricted, so the "+" stub never hides (unlike a
          capped connector slot). */}
      <Handle
        type="target"
        position={Position.Left}
        title="Connect to another agent (or a Critic Gate)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <MainEdgeAddStub nodeId={id} direction="incoming" />
      <div className="flex items-center gap-1.5">
        <Bot className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        {/* Renaming happens in the Inspector's own title now (click it,
            same as the experiment name) -- not here anymore. */}
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'Agent'}
        </span>
      </div>
      <NodeSummaryLine
        text={data.config?.prompt || data.config?.goal || null}
        warning={data.missingLlm ? "No LLM connected -- this agent can't run" : null}
      />
      {/* n8n's own 3 bottom sub-connectors (Chat Model/Memory/Tool), adapted
          and extended with a 4th (Architectural Pattern, ASAREE's own
          addition, no n8n equivalent): required LLM (exactly one), optional
          max-1 Architectural Pattern (capped for the execution-pattern
          category specifically -- see _EXECUTION_PATTERN_NODE_TYPES in
          protocol_execution.py) and Memory (visual scaffolding only -- see
          MemoryNodeData), and optional repeatable Tool. */}
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
        title="Architectural Pattern -- always exactly one; pick a node here to swap it"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="38%">Pattern</ConnectorHandleLabel>
      {/* Never hides once connected (unlike LLM/Memory) -- an execution
          pattern must never go to zero (agentic-core silently falls back
          to reason_act if left unconnected, undoing the whole point of
          making the default explicit), so the only way to change it is to
          replace it via this same stub, never a bare delete. See
          addNode()'s own pendingConnectorAdd branch for the replace logic,
          and ProtocolCanvas.tsx's nodesWithRunStatus for why the connected
          pattern node itself can't be deleted directly either. */}
      <ConnectorAddStub nodeId={id} slot="architectural_pattern" left="38%" alwaysVisible />
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
      <ConnectorAddStub nodeId={id} slot="tool" left="86%" alwaysVisible />
      <Handle
        type="source"
        position={Position.Right}
        title="Connect to another agent (or a Critic Gate)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <MainEdgeAddStub nodeId={id} direction="outgoing" />
    </div>
  )
}
