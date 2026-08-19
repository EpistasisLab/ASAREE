import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent, hashToChartHue } from '@/lib/utils'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { AgentNodeData, NodeRunStatus } from '@/types/protocols'
import { boundFactorCount, hasBoundFactor } from '../bindableFields'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { ConnectorAddStub } from './ConnectorAddStub'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { MainEdgeAddStub } from './MainEdgeAddStub'
import { NodeFactorBadge } from './NodeFactorBadge'
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
}: NodeProps & {
  data: AgentNodeData & { runStatus?: NodeRunStatus; missingLlm?: boolean; canRunAlone?: boolean }
}) {
  const badge = nodeRunBadge(data.runStatus)
  const { updateNodeData } = useReactFlow()
  const { requestRunNode } = useProtocolCanvasActions()
  const isActive = data.active ?? true

  return (
    <div
      style={cardAccent(ACCENT)}
      className={`group relative flex min-h-20 w-60 flex-col justify-center rounded-md border bg-card px-2.5 py-3.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
        selected ? 'ring-2 ring-[color:var(--card-accent)]' : ''
      } ${isActive ? '' : 'opacity-50'}`}
    >
      <NodeHoverToolbar
        nodeId={id}
        isActive={isActive}
        onToggleActive={() => updateNodeData(id, { active: !isActive })}
        runAlone={{ canRun: !!data.canRunAlone, onRun: () => requestRunNode(id) }}
      />
      {/* Steps left to `right-6` when the factor badge is also showing: that
          badge straddles this same corner (-right-3, size-7, so 12px out to
          16px in) and this Badge straddles the top border (-top-2.5, h-5), so
          at the default right-1.5 the two would sit on top of each other. */}
      {badge && (
        <Badge className={`absolute -top-2.5 ${hasBoundFactor(data) ? 'right-6' : 'right-1.5'} ${badge.className}`}>
          {badge.label}
        </Badge>
      )}
      {/* Top-RIGHT, matching every other node shape (CircleNode hangs the same
          badge off its own top-right). Hung ON the corner rather than tucked
          inside it: at size-7 an inset badge would blanket most of the icon/
          label row, and half-overlapping the border reads as "attached to this
          node" anyway. Top-center (on hover) is NodeHoverToolbar, and the
          Architectural Pattern connector's own label/stub live OUTSIDE the
          card on the left of this edge, so neither competes for this corner. */}
      {hasBoundFactor(data) && <NodeFactorBadge count={boundFactorCount(data)} className="-top-3 -right-3" />}
      {/* Main flow is left-to-right -- the 4 sub-connectors below stay on the
          bottom edge regardless, since a config source hangs below a node no
          matter which way the main flow runs.

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
      {/* Architectural Pattern sits on the TOP edge, on its own -- it's a
          swap-only, never-zero connector (see its own comment below), a
          different enough interaction from the other three that giving it
          its own edge reads more clearly than crowding it into the bottom
          row. Offset to the top-LEFT rather than dead-center: the hover
          toolbar (NodeHoverToolbar) already claims the top-center span
          (-top-8, appearing on :hover) and the run-status Badge already
          claims the top-right corner, so top-left is the one open zone on
          this edge left for a third occupant. The 3 bottom sub-connectors:
          required LLM (exactly one), optional max-1 Memory (visual
          scaffolding only -- see MemoryNodeData), and optional repeatable
          Tool. Dataset and Script are pure config sources too, but
          deliberately do NOT get their own slot -- they wire into this same
          Tool connector (one connector accepting a FAMILY of node types,
          matching Motoro's own
          _NODE_TYPE_TO_HANDLE): the Tool "+" panel's search just lists
          mcp_tool/Dataset/Script side by side (see CONNECTOR_PANEL_INFO.tool's
          allowedTypes in ProtocolCanvas.tsx), and which sub-kind a given
          wired node actually is gets recovered from its own node `type`, not
          from a dedicated handle. */}
      <Handle
        type="target"
        id="architectural_pattern"
        position={Position.Top}
        style={{ left: '15%' }}
        title="Architectural Pattern -- always exactly one; pick a node here to swap it"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="15%" side="top">Pattern</ConnectorHandleLabel>
      {/* Never hides once connected (unlike LLM/Memory) -- an execution
          pattern must never go to zero (Motoro silently falls back
          to reason_act if left unconnected, undoing the whole point of
          making the default explicit), so the only way to change it is to
          replace it via this same stub, never a bare delete. See
          addNode()'s own pendingConnectorAdd branch for the replace logic,
          and ProtocolCanvas.tsx's nodesWithRunStatus for why the connected
          pattern node itself can't be deleted directly either. */}
      <ConnectorAddStub nodeId={id} slot="architectural_pattern" left="15%" side="top" alwaysVisible />
      <Handle
        type="target"
        id="llm"
        position={Position.Bottom}
        style={{ left: '20%' }}
        title="LLM (required)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="20%">LLM</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="llm" left="20%" />
      <Handle
        type="target"
        id="memory"
        position={Position.Bottom}
        style={{ left: '50%' }}
        title="Memory (not yet functional)"
        className="!size-2 !border-2 !border-dashed !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="50%">Memory</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="memory" left="50%" />
      <Handle
        type="target"
        id="tool"
        position={Position.Bottom}
        style={{ left: '80%' }}
        title="Tool -- MCP server, Dataset, or Script"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="80%">Tool</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="tool" left="80%" alwaysVisible />
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
