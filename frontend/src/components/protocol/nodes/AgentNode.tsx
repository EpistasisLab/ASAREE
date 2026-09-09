import { Handle, Position, useReactFlow, type NodeProps } from '@xyflow/react'
import { Bot } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { cardAccent } from '@/lib/utils'
import { nodeAccent } from '@/lib/nodeAccent'
import { nodeRunBadge } from '@/lib/protocolRun'
import type { AgentNodeData, NodeRunStatus } from '@/types/protocols'
import { boundFactorCount, hasBoundFactor } from '../bindableFields'
import { connectorLefts } from '../layout'
import { useProtocolCanvasActions } from '../ProtocolCanvasContext'
import { useProviderModels } from '../useProviderModels'
import { ConnectorAddStub } from './ConnectorAddStub'
import { ConnectorHandleLabel } from './ConnectorHandleLabel'
import { MainEdgeAddStub } from './MainEdgeAddStub'
import { NodeFactorBadge } from './NodeFactorBadge'
import { NodeHoverToolbar } from './NodeHoverToolbar'
import { NodeSummaryLine } from './NodeSummaryLine'

// All "agent" nodes share one hue -- color on this canvas is type-based (see
// lib/nodeAccent.ts), not per-instance, since a node's identity is its type,
// not its label. The connector captions below are the one thing here that
// does NOT follow --card-accent: they're yellow, to be found against the node
// rather than to match it (ConnectorHandleLabel).
const ACCENT = nodeAccent('agent')

// Every connector's handle, caption and "+" stub reads its x from here, and so
// does the placement of whatever node the connector's own "+" creates (see
// layout.ts) -- otherwise a node can land under a connector that isn't the one
// that asked for it.
const CONNECTOR_LEFT = connectorLefts('agent')

export function AgentNode({
  id,
  data,
  selected,
}: NodeProps & {
  data: AgentNodeData & {
    runStatus?: NodeRunStatus
    missingLlm?: boolean
    canRunAlone?: boolean
    // Both injected by ProtocolCanvas: whether a plain Agent-to-Agent edge
    // reaches this node, and the model its AI connector resolves to. The
    // canvas supplies the wiring; the capability lookup below is this card's.
    hasPeers?: boolean
    llmConfig?: { provider?: string; model?: string } | null
    // Also the canvas's: which role `data.conversation_lead` amounts to under
    // the experiment's coordination strategy -- 'lead' under Peer
    // Collaboration, 'supervisor' under Supervisor, and null under a strategy
    // that ignores the marker. Not the raw flag: the strategy lives on the
    // experiment, which this card doesn't query.
    leadRole?: 'lead' | 'supervisor' | null
    // The canvas's too: whether this node's main-flow sides can still take an
    // edge. Only ever true under Sequential, where the chain rule caps each at
    // one -- see ProtocolCanvas's `mainEdgeSlots`.
    mainInFull?: boolean
    mainOutFull?: boolean
  }
}) {
  const badge = nodeRunBadge(data.runStatus)
  // Peers are offered to the model as function schemas -- that is the only
  // channel a consultation can be *chosen* through -- so an agent on a model
  // that can't accept them would know its peers exist and never be able to
  // ask one. Not a misconfiguration (the run succeeds, the agent just works
  // alone), so it's a card warning rather than a findNodeConfigIssues entry
  // that interrupts a Run -- the same call the ReAct "this loop won't loop"
  // warning makes. `supports_tool_calling` is null for a model litellm
  // doesn't know, which is "can't tell", so only an explicit false warns.
  const { models } = useProviderModels(data.hasPeers ? data.llmConfig?.provider : undefined)
  const peerNeedsToolCalling =
    !!data.hasPeers && models.find((m) => m.id === data.llmConfig?.model)?.supports_tool_calling === false
  const warnings = [
    ...(data.missingLlm ? ["No AI connected -- this agent can't run"] : []),
    ...(peerNeedsToolCalling ? ["This model can't call tools, so this agent can't consult its connected peers"] : []),
  ]
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
          runtime, see design_spec.coordination_strategy). That same strategy
          decides the cardinality: unrestricted under Peer Collaboration and
          Critic Gate, but exactly one per side under Sequential, where the
          chain rule applies and the "+" stub hides once a side is taken. */}
      <Handle
        type="target"
        position={Position.Left}
        title="Connect to another agent (or a Critic Gate)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <MainEdgeAddStub nodeId={id} direction="incoming" full={data.mainInFull} />
      <div className="flex items-center gap-1.5">
        <Bot className="size-3.5 shrink-0 text-[color:var(--card-accent)]" />
        {/* Renaming happens in the Inspector's own title now (click it,
            same as the experiment name) -- not here anymore. */}
        <span className="truncate text-xs font-medium" title={data.label}>
          {data.label || 'Agent'}
        </span>
        {/* Inline on the title row rather than hung off a corner: all three
            corners are spoken for (run status and the factor badge share the
            top-right, the Pattern/Skill captions sit above the top-left), and
            "this is the agent that leads" reads as part of the node's identity
            anyway. `outline` so it states a role without competing with the
            run-status badge, which is the thing that actually changes. */}
        {data.leadRole && (
          <Badge
            variant="outline"
            title={
              data.leadRole === 'supervisor'
                ? 'This agent briefs the workers wired to it and writes the final answer, which is the recorded result'
                : 'The conversation starts here -- this agent gets the task and its answer is the recorded result'
            }
            className="h-4 shrink-0 border-[color:var(--card-accent)]/60 px-1.5 text-[10px] text-[color:var(--card-accent)]"
          >
            {data.leadRole === 'supervisor' ? 'Supervisor' : 'Lead'}
          </Badge>
        )}
      </div>
      <NodeSummaryLine
        text={data.config?.prompt || data.config?.goal || null}
        warning={warnings.length > 0 ? warnings : null}
      />
      {/* FOUR connectors live on the TOP edge -- Pattern, Skill, Dataset,
          Knowledge, in that reading order -- all of them "what this agent IS
          configured with" rather than a runtime capability, which is what
          separates them from the bottom row. Their x-positions are NOT evenly
          spaced, and that's forced, not a style choice: the hover toolbar
          (NodeHoverToolbar, -top-8, appearing on :hover with a solid bg-card)
          is ~112px wide and centered, so it owns the middle of this card and
          would sit right on top of any "+" stub placed there -- all four have
          to fit in the two margins outside it. That's also why the card is
          w-72 rather than the w-60 it was with three: at 240px the margins are
          64px each, which two centered captions won't fit in; at 288px they're
          88px. Hence a Pattern/Skill pair in the left margin (5% / 18%) and a
          Dataset/Knowledge pair in the right (71% / 90%) -- the numbers
          themselves live in layout.ts's CONNECTOR_X, because addNode() has to
          place a connector's node at that same x -- each spaced so neither
          their ~26px stubs nor their centered captions collide. Every
          caption is centered directly above its own handle, mirroring the
          bottom row (see ConnectorHandleLabel's side="top" branch).

          Dataset is at 71% rather than the 74% it started at: "Dataset" and
          "Knowledge" are the two longest captions on this edge, and centered
          at 74/90 they were within a pixel or two of touching. 71% is about
          as far left as it can go -- the toolbar's right edge is at ~69%, and
          the stub's own padding already overlaps it slightly (harmless, the
          toolbar is above the stub's z-index and only there on hover, and the
          visible "+" glyph still clears it).

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
        style={{ left: CONNECTOR_LEFT.architectural_pattern }}
        title="Architectural Pattern -- always exactly one; pick a node here to swap it"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.architectural_pattern} side="top">Pattern</ConnectorHandleLabel>
      {/* Never hides once connected (unlike AI/Memory) -- an execution
          pattern must never go to zero (Motoro silently falls back
          to reason_act if left unconnected, undoing the whole point of
          making the default explicit), so the only way to change it is to
          replace it via this same stub, never a bare delete. See
          addNode()'s own pendingConnectorAdd branch for the replace logic,
          and ProtocolCanvas.tsx's nodesWithRunStatus for why the connected
          pattern node itself can't be deleted directly either. */}
      <ConnectorAddStub nodeId={id} slot="architectural_pattern" left={CONNECTOR_LEFT.architectural_pattern} side="top" alwaysVisible />
      {/* Skill -- registered Agent Skills, one skill directory each, stored
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
        style={{ left: CONNECTOR_LEFT.skill }}
        title="Skill -- Agent Skills this agent can open; add as many as you like"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.skill} side="top">Skill</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="skill" left={CONNECTOR_LEFT.skill} side="top" alwaysVisible />
      {/* Dataset -- the data an agent works ON, as opposed to the Tool
          connector's "capabilities it works WITH". UNCAPPED, like
          Skill/Knowledge/Tool: a cell's workspace holds one dataset per named
          SLOT (dataset:<name>), so several wired datasets each get their own
          independently staged lineage and the agent picks between them by
          passing slot="..." to the workspace tools. Wiring order is the order
          _build_user_input lists them in. It was capped at one for a while
          (one workspace, one dataset, seed_cell_workspace refusing a second),
          which is why the cap came and went before slots existed.
          Still distinct from COMPARING datasets, which is a FACTOR
          (levelType 'dataset_config' -- the inspector title row's "Make
          factor" button): that varies WHICH dataset a cell gets, rather than
          giving one cell several at once.
          The slot is named after the node type because that node is its only
          member; it was briefly called "Resource", and before that it shared
          the Tool slot outright, so older graphs carry dataset edges on
          targetHandle "resource" or "tool" -- migrateLegacyHandles rewrites
          both to "dataset" on load, and the backend keeps accepting all three
          (see _LEGACY_DATASET_HANDLES).

          Placed immediately right of Skill so the top edge reads
          Pattern -> Skill -> Dataset -> Knowledge: the two slots naming a
          registered artifact the user picked from a browser (Skill, Dataset)
          sit next to each other, rather than being split by the margin gap. */}
      <Handle
        type="target"
        id="dataset"
        position={Position.Top}
        style={{ left: CONNECTOR_LEFT.dataset }}
        title="Dataset -- the registered datasets this agent operates on (each gets its own workspace slot)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.dataset} side="top">Dataset</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="dataset" left={CONNECTOR_LEFT.dataset} side="top" alwaysVisible />
      {/* Knowledge -- registered OKF bundles, each a directory of Markdown
          concepts on the SERVER's disk that the agent reads AND writes as it
          works (see OkfBundleNodeData). Its own slot rather than sharing
          Tool's, even though a bundle is mechanically just another MCP server
          (its tools are merged into the same allow-list by
          _resolve_knowledge_config): what the user is declaring here is a
          knowledge base, not one more capability, and that distinction would
          be lost among five servers on the Tool slot.

          Last on this edge, at 90%: it's the softest, longest-lived thing an
          agent is configured with (a knowledge base that outlives the run),
          so it sits furthest from the Pattern/Skill/Dataset run-shaping
          group. Its centered caption can graze the top of the run-status
          Badge on that corner while a run is in flight; the caption is the
          secondary read there, and moving it off-center again would undo the
          "every connector's label sits on the connector" rule.

          Repeatable and UNCAPPED, like Skill and Tool: reading a shared team
          bundle while writing to a personal one is a normal setup. So its "+"
          stub stays visible after the first connection. */}
      <Handle
        type="target"
        id="knowledge"
        position={Position.Top}
        style={{ left: CONNECTOR_LEFT.knowledge }}
        title="Knowledge -- OKF bundles this agent can read and write; add as many as you like"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.knowledge} side="top">Knowledge</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="knowledge" left={CONNECTOR_LEFT.knowledge} side="top" alwaysVisible />
      {/* Handle id `ai`; graphs saved before the rename carry these edges on
          `llm` -- ProtocolCanvas.tsx rewrites those on load
          (migrateLegacyHandles) and the backend keeps accepting both (see
          _LEGACY_AI_HANDLES). The node types feeding it are still called
          LLM_NODE_TYPES: those name a model family, not this slot. */}
      <Handle
        type="target"
        id="ai"
        position={Position.Bottom}
        style={{ left: CONNECTOR_LEFT.ai }}
        title="AI (required)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.ai}>AI</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="ai" left={CONNECTOR_LEFT.ai} />
      <Handle
        type="target"
        id="memory"
        position={Position.Bottom}
        style={{ left: CONNECTOR_LEFT.memory }}
        title="Memory (not yet functional)"
        className="!size-2 !border-2 !border-dashed !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.memory}>Memory</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="memory" left={CONNECTOR_LEFT.memory} />
      <Handle
        type="target"
        id="tool"
        position={Position.Bottom}
        style={{ left: CONNECTOR_LEFT.tool }}
        title="Tool -- MCP server, Dataset, or Script"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <ConnectorHandleLabel left={CONNECTOR_LEFT.tool}>Tool</ConnectorHandleLabel>
      <ConnectorAddStub nodeId={id} slot="tool" left={CONNECTOR_LEFT.tool} alwaysVisible />
      <Handle
        type="source"
        position={Position.Right}
        title="Connect to another agent (or a Critic Gate)"
        className="!size-2 !border-2 !bg-background !border-[color:var(--card-accent)]"
      />
      <MainEdgeAddStub nodeId={id} direction="outgoing" full={data.mainOutFull} />
    </div>
  )
}
