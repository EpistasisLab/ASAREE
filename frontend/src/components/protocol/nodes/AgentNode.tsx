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
      className={`group relative flex min-h-20 w-72 flex-col justify-center rounded-md border bg-card px-2.5 py-3.5 shadow-[0_0_12px_-6px_var(--card-accent)] ring-1 ring-[color:var(--card-accent)]/40 ${
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
        warning={data.missingLlm ? "No AI connected -- this agent can't run" : null}
      />
      {/* FOUR connectors live on the TOP edge -- Pattern, Skill, Knowledge,
          Resource -- all of them "what this agent IS configured with" rather
          than a runtime capability, which is what separates them from the
          bottom row. Their x-positions are NOT evenly spaced, and that's
          forced, not a style choice: the hover toolbar (NodeHoverToolbar,
          -top-8, appearing on :hover with a solid bg-card) is ~112px wide and
          centered, so it owns the middle of this card and would sit right on
          top of any "+" stub placed there -- all four have to fit in the two
          margins outside it. That's also why the card is w-72 rather than the
          w-60 it was with three: at 240px the margins are 64px each, which two
          centered captions won't fit in; at 288px they're 88px. Hence a
          Pattern/Skill pair in the left margin (5% / 18%) and a
          Knowledge/Resource pair in the right (74% / 90%), each spaced so
          neither their ~26px stubs nor their centered captions collide. Every
          caption is centered directly above its own handle, mirroring the
          bottom row (see ConnectorHandleLabel's side="top" branch).

          The 3 bottom sub-connectors: required AI (exactly one), optional
          max-1 Memory (visual scaffolding only -- see MemoryNodeData), and
          optional repeatable Tool. Script is a pure config source too, but
          deliberately does NOT get its own slot -- it wires into that same
          Tool connector (one connector accepting a FAMILY of node types,
          matching Motoro's own
          _NODE_TYPE_TO_HANDLE): the Tool "+" panel's search just lists
          mcp_tool/Script side by side (see CONNECTOR_PANEL_INFO.tool's
          allowedTypes in ProtocolCanvas.tsx), and which sub-kind a given
          wired node actually is gets recovered from its own node `type`, not
          from a dedicated handle. */}
      <Handle
        type="target"
        id="architectural_pattern"
        position={Position.Top}
        style={{ left: '5%' }}
        title="Architectural Pattern -- always exactly one; pick a node here to swap it"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="5%" side="top">Pattern</ConnectorHandleLabel>
      {/* Never hides once connected (unlike AI/Memory) -- an execution
          pattern must never go to zero (Motoro silently falls back
          to reason_act if left unconnected, undoing the whole point of
          making the default explicit), so the only way to change it is to
          replace it via this same stub, never a bare delete. See
          addNode()'s own pendingConnectorAdd branch for the replace logic,
          and ProtocolCanvas.tsx's nodesWithRunStatus for why the connected
          pattern node itself can't be deleted directly either. */}
      <ConnectorAddStub nodeId={id} slot="architectural_pattern" left="5%" side="top" alwaysVisible />
      {/* Skill -- registered Agent Skills, one SKILL.md document each, stored
          server-side and referenced by id (see SkillNodeData). Repeatable and
          UNCAPPED, like Tool and unlike everything else on this edge:
          carrying several skills is the normal case, since each costs only
          its ~100-token name+description until the model actually opens one
          (Motoro's engine/skills.py does the progressive disclosure). So its
          "+" stub stays visible after the first connection. */}
      <Handle
        type="target"
        id="skill"
        position={Position.Top}
        style={{ left: '18%' }}
        title="Skill -- Agent Skills this agent can open; add as many as you like"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="18%" side="top">Skill</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="skill" left="18%" side="top" alwaysVisible />
      {/* Knowledge -- registered OKF bundles, each a directory of Markdown
          concepts on the SERVER's disk that the agent reads AND writes as it
          works (see OkfBundleNodeData). Its own slot rather than sharing
          Tool's, even though a bundle is mechanically just another MCP server
          (its tools are merged into the same allow-list by
          _resolve_knowledge_config): what the user is declaring here is a
          knowledge base, not one more capability, and that distinction would
          be lost among five servers on the Tool slot.

          Left of Resource, and in that order deliberately -- Knowledge and
          Resource are both "data this agent works on," with Knowledge the
          softer, longer-lived one (a knowledge base that outlives the run)
          and Resource the concrete dataset this run operates on, so the
          rightmost slot stays the one nearest the run itself.

          Repeatable and UNCAPPED, like Skill and Tool: reading a shared team
          bundle while writing to a personal one is a normal setup. So its "+"
          stub stays visible after the first connection. */}
      <Handle
        type="target"
        id="knowledge"
        position={Position.Top}
        style={{ left: '74%' }}
        title="Knowledge -- OKF bundles this agent can read and write; add as many as you like"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="74%" side="top">Knowledge</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="knowledge" left="74%" side="top" alwaysVisible />
      {/* Resource -- the data an agent works ON, as opposed to the Tool
          connector's "capabilities it works WITH". Today that's a Dataset
          node (max one, matching protocol_execution.py's own "at most one
          Dataset connection" cap); it used to share the Tool slot, and
          graphs saved back then still carry dataset edges on targetHandle
          "tool" -- migrateLegacyHandles rewrites those to "resource" on load,
          and the backend keeps accepting both (see _LEGACY_DATASET_HANDLES).

          Paired with Knowledge in the right-hand margin at 90% -- see this
          edge's own placement note above. Its centered caption can graze the
          top of the run-status Badge on that corner while a run is in
          flight; the
          caption is the secondary read there, and moving it off-center again
          would undo the "every connector's label sits on the connector"
          rule. */}
      <Handle
        type="target"
        id="resource"
        position={Position.Top}
        style={{ left: '90%' }}
        title="Resource -- the Dataset this agent operates on"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="90%" side="top">Resource</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="resource" left="90%" side="top" />
      {/* Handle id `ai`; graphs saved before the rename carry these edges on
          `llm` -- ProtocolCanvas.tsx rewrites those on load
          (migrateLegacyHandles) and the backend keeps accepting both (see
          _LEGACY_AI_HANDLES). The node types feeding it are still called
          LLM_NODE_TYPES: those name a model family, not this slot. */}
      <Handle
        type="target"
        id="ai"
        position={Position.Bottom}
        style={{ left: '20%' }}
        title="AI (required)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left="20%">AI</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="ai" left="20%" />
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
