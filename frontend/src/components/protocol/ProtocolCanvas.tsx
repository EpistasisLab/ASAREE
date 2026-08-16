import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addEdge,
  Background,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  useViewport,
  type Connection,
  type Edge,
  type Node,
  type ReactFlowInstance,
  type Viewport,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Play, Plus, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { newNodeId } from '@/lib/nodeId'
import { protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import { TERMINAL_RUN_STATUSES } from '@/lib/protocolRun'
import {
  defaultAgentNodeData,
  defaultAnthropicLlmNodeData,
  defaultAzureFoundryLlmNodeData,
  defaultCriticGateNodeData,
  defaultDatasetNodeData,
  defaultMcpToolNodeData,
  defaultMemoryNodeData,
  defaultOpenAiLlmNodeData,
  defaultReasonActPatternNodeData,
  defaultScriptNodeData,
  defaultSingleAgentBaselinePatternNodeData,
} from '@/types/protocols'
import type {
  AgentNodeData,
  CriticGateNodeData,
  DatasetNodeData,
  LlmNodeData,
  McpToolNodeData,
  MemoryNodeData,
  ProtocolGraph,
  ProtocolNode,
  ReasonActPatternNodeData,
  ScriptNodeData,
  SingleAgentBaselinePatternNodeData,
} from '@/types/protocols'
import type { DesignFactor } from '@/types/experiments'
import { AddNodePanel } from './AddNodePanel'
import { AgentNodeInspector } from './AgentNodeInspector'
import { agentTracedLabel, unboundBindableFields, type UnboundField } from './bindableFields'
import { CanvasControls } from './CanvasControls'
import { CriticGateNodeInspector } from './CriticGateNodeInspector'
import { DatasetNodeInspector } from './DatasetNodeInspector'
import { DeleteNodeConfirmDialog } from './DeleteNodeConfirmDialog'
import { DEFAULT_ZOOM } from './constants'
import { FactorEditorDialog } from './FactorEditorDialog'
import { findFreePosition } from './layout'
import { LlmNodeInspector } from './LlmNodeInspector'
import { McpToolNodeInspector } from './McpToolNodeInspector'
import { MemoryNodeInspector } from './MemoryNodeInspector'
import {
  ProtocolCanvasActionsProvider,
  type ConnectorAddRequest,
  type ConnectorSlot,
  type EdgeInsertRequest,
  type MainEdgeAddRequest,
} from './ProtocolCanvasContext'
import { ReasonActPatternNodeInspector } from './ReasonActPatternNodeInspector'
import { ScriptNodeInspector } from './ScriptNodeInspector'
import { SingleAgentBaselinePatternNodeInspector } from './SingleAgentBaselinePatternNodeInspector'
import { InteractEdge } from './edges/InteractEdge'
import { AgentNode } from './nodes/AgentNode'
import { CriticGateNode } from './nodes/CriticGateNode'
import { DatasetNode } from './nodes/DatasetNode'
import { LlmNode } from './nodes/LlmNode'
import { McpToolNode } from './nodes/McpToolNode'
import { MemoryNode } from './nodes/MemoryNode'
import { ReasonActPatternNode } from './nodes/ReasonActPatternNode'
import { ScriptNode } from './nodes/ScriptNode'
import { SingleAgentBaselinePatternNode } from './nodes/SingleAgentBaselinePatternNode'
import { ProtocolCanvasMenu } from './ProtocolCanvasMenu'

// One node type per LLM provider / architectural pattern (see LlmNodeData/
// ReasonActPatternNodeData's own comments in types/protocols.ts) -- each
// connector slot accepts this whole family, not one exact type, mirroring
// how the "tool" slot already accepts any mcp_tool node.
const LLM_NODE_TYPES = ['llm_anthropic', 'llm_openai', 'llm_azure_foundry']
const PATTERN_NODE_TYPES = ['pattern_reason_act', 'pattern_single_agent_baseline']
// Mirrors services.protocol_execution's own _CONNECTOR_HANDLES -- any edge
// whose targetHandle ISN'T one of these is a plain "main" pipeline edge.
const CONNECTOR_HANDLES = new Set(['llm', 'tool', 'memory', 'architectural_pattern'])

const NODE_TYPES = {
  agent: AgentNode,
  mcp_tool: McpToolNode,
  critic_gate: CriticGateNode,
  // All three LLM provider types render through the same component -- it
  // derives icon/accent/placeholder from data.config.provider, not from
  // which of these three keys it was registered under.
  llm_anthropic: LlmNode,
  llm_openai: LlmNode,
  llm_azure_foundry: LlmNode,
  memory: MemoryNode,
  dataset: DatasetNode,
  script: ScriptNode,
  pattern_reason_act: ReasonActPatternNode,
  pattern_single_agent_baseline: SingleAgentBaselinePatternNode,
}
// Every edge (plain or connector) renders through InteractEdge -- no edge
// ever has an explicit `type`, so overriding xyflow's own built-in
// "default" key covers all of them, matching how none of them are wired
// via a separate registered edge type.
const EDGE_TYPES = { default: InteractEdge }
const AUTOSAVE_DELAY_MS = 800
const RUN_POLL_MS = 2000

// x/y are pixel offsets of the flow's translation (screen space, not flow
// coordinates), so a 1px tolerance is "same place" regardless of how large
// the node layout is. zoom is a unitless scale factor -- same epsilon
// CanvasControls already uses for its own "is this the default zoom" check.
const VIEWPORT_EPSILON_XY = 1
const VIEWPORT_EPSILON_ZOOM = 0.01

function isNearViewport(a: Viewport, b: Viewport): boolean {
  return (
    Math.abs(a.x - b.x) < VIEWPORT_EPSILON_XY &&
    Math.abs(a.y - b.y) < VIEWPORT_EPSILON_XY &&
    Math.abs(a.zoom - b.zoom) < VIEWPORT_EPSILON_ZOOM
  )
}

function defaultDataFor(nodeType: string): ProtocolNode['data'] {
  if (nodeType === 'mcp_tool') return defaultMcpToolNodeData()
  if (nodeType === 'critic_gate') return defaultCriticGateNodeData()
  if (nodeType === 'llm_anthropic') return defaultAnthropicLlmNodeData()
  if (nodeType === 'llm_openai') return defaultOpenAiLlmNodeData()
  if (nodeType === 'llm_azure_foundry') return defaultAzureFoundryLlmNodeData()
  if (nodeType === 'memory') return defaultMemoryNodeData()
  if (nodeType === 'dataset') return defaultDatasetNodeData()
  if (nodeType === 'script') return defaultScriptNodeData()
  if (nodeType === 'pattern_reason_act') return defaultReasonActPatternNodeData()
  if (nodeType === 'pattern_single_agent_baseline') return defaultSingleAgentBaselinePatternNodeData()
  return defaultAgentNodeData()
}

// Mirrors isValidConnection's own per-slot source-type-family rule -- the
// panel that opens for a connector "+" is pre-filtered to that slot's whole
// family of node types (LLM_NODE_TYPES/PATTERN_NODE_TYPES above) rather than
// the full catalog. Tool's own family includes Dataset/Script alongside
// mcp_tool (n8n's own convention for a connector that accepts several kinds
// of node -- see AgentNode.tsx's own comment on its Tool handle) -- both are
// pure config sources with no callable capability of their own, so they
// share Tool's slot rather than getting a dedicated one.
const CONNECTOR_PANEL_INFO: Record<ConnectorSlot, { allowedTypes: string[]; title: string }> = {
  llm: { allowedTypes: LLM_NODE_TYPES, title: 'Add LLM' },
  tool: { allowedTypes: ['mcp_tool', 'dataset', 'script'], title: 'Add Tool' },
  memory: { allowedTypes: ['memory'], title: 'Add Memory' },
  architectural_pattern: { allowedTypes: PATTERN_NODE_TYPES, title: 'Add Architectural Pattern' },
}

// Imperative, not a prop -- DesignTab.tsx (a sibling of ProtocolCanvas, not
// a descendant) needs to write a factor binding onto a specific canvas
// node the moment the user picks it from the "Add factor" dropdown. This is
// the one thing the shared protocol-graph query cache (see
// protocolGraphQueryKey) can't do on its own: that cache is a read-only
// mirror of ProtocolCanvas's own nodes/edges state, so writing into it
// directly wouldn't flow back into the actual xyflow state driving the
// canvas. A narrow, purpose-built handle (just this one method) is enough,
// rather than exposing setNodes/setEdges wholesale.
export interface ProtocolCanvasHandle {
  bindFactor: (nodeId: string, fieldPath: string, factorName: string) => void
  // Sweeps EVERY node's factor_bindings, dropping any entry pointing at
  // `factorName` -- called when a factor is deleted via the Design tab's
  // FactorsEditor, so a canvas node never stays silently "bound" to a
  // factor that no longer exists in design_spec.factors (without this, the
  // node's own FactorBindableField keeps showing a "Factor: {name}" badge
  // for a name that resolves to nothing, and the field can never be
  // re-bound since unboundBindableFields treats any non-empty
  // factor_bindings entry as still bound).
  removeFactorBindings: (factorName: string) => void
  // Same idea for a rename (FactorsEditor's Edit dialog can change a
  // factor's own `name`) -- every node bound to `oldName` gets repointed to
  // `newName` instead of being left referencing a name that no longer
  // resolves.
  renameFactorBindings: (oldName: string, newName: string) => void
}

export const ProtocolCanvas = forwardRef<ProtocolCanvasHandle, {
  protocolId: string
  experimentId: string | null
  initialGraph: ProtocolGraph
}>(function ProtocolCanvas({ protocolId, experimentId, initialGraph }, canvasHandleRef) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialGraph.nodes as Node[])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialGraph.edges as Edge[])
  const queryClient = useQueryClient()

  // Mirrors the canvas's own live state into the shared query cache on
  // every change -- immediate, not debounced like autosave below, since
  // this is a pure in-memory write (no network round-trip to wait on).
  // DesignTab's "Add factor" picker reads this same key to always see the
  // canvas's current nodes, never the last-autosaved snapshot.
  useEffect(() => {
    queryClient.setQueryData(protocolGraphQueryKey(protocolId), { nodes, edges })
  }, [queryClient, protocolId, nodes, edges])

  // Shared by DesignTab's own bindFactor (via the imperative handle below)
  // and this canvas's own per-node "Make experimental factor" hover-toolbar
  // button (requestMakeFactor/factorPickerMutation below) -- both write the
  // same node-side half of a binding, just from two different entry points.
  const bindFactorOnNode = useCallback(
    (nodeId: string, fieldPath: string, factorName: string) => {
      setNodes((nds) =>
        nds.map((n) =>
          n.id === nodeId
            ? { ...n, data: { ...n.data, factor_bindings: { ...(n.data.factor_bindings as Record<string, string> | undefined), [fieldPath]: factorName } } }
            : n,
        ),
      )
    },
    [setNodes],
  )

  // Both sweep every node's factor_bindings by VALUE (the factor name),
  // not by any specific nodeId/fieldPath -- a factor can be bound from more
  // than one node/field, and the caller (FactorsEditor) has no reason to
  // know which ones without duplicating this same scan itself.
  const removeFactorBindings = useCallback(
    (factorName: string) => {
      setNodes((nds) =>
        nds.map((n) => {
          const bindings = n.data.factor_bindings as Record<string, string> | undefined
          if (!bindings || !Object.values(bindings).includes(factorName)) return n
          const next = Object.fromEntries(Object.entries(bindings).filter(([, name]) => name !== factorName))
          return { ...n, data: { ...n.data, factor_bindings: next } }
        }),
      )
    },
    [setNodes],
  )
  const renameFactorBindings = useCallback(
    (oldName: string, newName: string) => {
      setNodes((nds) =>
        nds.map((n) => {
          const bindings = n.data.factor_bindings as Record<string, string> | undefined
          if (!bindings || !Object.values(bindings).includes(oldName)) return n
          const next = Object.fromEntries(Object.entries(bindings).map(([path, name]) => [path, name === oldName ? newName : name]))
          return { ...n, data: { ...n.data, factor_bindings: next } }
        }),
      )
    },
    [setNodes],
  )

  useImperativeHandle(
    canvasHandleRef,
    () => ({ bindFactor: bindFactorOnNode, removeFactorBindings, renameFactorBindings }),
    [bindFactorOnNode, removeFactorBindings, renameFactorBindings],
  )
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  // The node whose hover-toolbar "Make experimental factor" icon was just
  // clicked -- opens FactorEditorDialog's field picker pre-filtered to just
  // that node's own unbound fields (see requestMakeFactor below).
  const [factorPickerNodeId, setFactorPickerNodeId] = useState<string | null>(null)
  const [addPanelOpen, setAddPanelOpen] = useState(false)
  // A pending node deletion awaiting user confirmation -- populated either
  // by onBeforeDelete (Backspace/Delete key, the hover toolbar's trash
  // icon -- both go through xyflow's own deleteElements) or by
  // requestDeleteNode (the node inspector's own Delete button, which calls
  // deleteNode directly and never touches xyflow's delete pipeline at all).
  // `resolve` is only set for the onBeforeDelete path -- xyflow is awaiting
  // it to decide whether the deletion actually proceeds.
  const [pendingDelete, setPendingDelete] = useState<{
    nodes: Node[]
    edges: Edge[]
    resolve?: (result: boolean | { nodes: Node[]; edges: Edge[] }) => void
  } | null>(null)
  // n8n-style dismissible error banner (own close button, no auto-hide) --
  // reset on every new Run click so a fresh attempt always gets a clean
  // slate even if the previous failure was never dismissed.
  const [runErrorDismissed, setRunErrorDismissed] = useState(false)
  // Set only while the "+" panel was opened via a connector stub (as
  // opposed to the canvas's own unrestricted toolbar "+") -- addNode()
  // branches on this to wire the new node into the requesting node's slot
  // and open its Inspector immediately, instead of dropping it unconnected
  // near the viewport center.
  const [pendingConnectorAdd, setPendingConnectorAdd] = useState<ConnectorAddRequest | null>(null)
  // Same idea, requested from a MainEdgeAddStub instead -- always creates
  // another Agent, wired via a plain edge (no handle id) in whichever
  // direction the requesting stub sits.
  const [pendingMainEdgeAdd, setPendingMainEdgeAdd] = useState<MainEdgeAddRequest | null>(null)
  // Same idea again, requested from an existing edge's own hover "+"
  // (InteractEdge) -- splits that edge into origin->newAgent->target.
  const [pendingEdgeInsert, setPendingEdgeInsert] = useState<EdgeInsertRequest | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  const paneRef = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition } = useReactFlow()

  // The canvas's "resting" viewport isn't a fixed constant -- fitView (set
  // below) recomputes x/y/zoom from the actual node layout on mount, so we
  // capture it once via onInit rather than assuming defaultViewport. onInit
  // only fires after xyflow's own viewportInitialized flips true, which is
  // after the declarative fitView has resolved -- until it fires, ref.current
  // is null and we treat the canvas as at rest (MiniMap stays hidden), which
  // is also the correct look while fitView is still settling.
  const restingViewportRef = useRef<Viewport | null>(null)
  const onCanvasInit = useCallback((instance: ReactFlowInstance) => {
    restingViewportRef.current = instance.getViewport()
  }, [])
  const currentViewport = useViewport()
  const isAtRest = !restingViewportRef.current || isNearViewport(currentViewport, restingViewportRef.current)

  const onConnect = useCallback((connection: Connection) => setEdges((eds) => addEdge(connection, eds)), [setEdges])

  const runMutation = useMutation({
    mutationFn: () => protocolsApi.run(protocolId),
    onSuccess: (run) => setRunId(run.id),
  })

  // The canvas's per-node Play icon -- reuses the exact same runId/runQuery
  // polling state as the main Run button, since a node-scoped run's
  // node_runs just carries one key instead of the whole graph's; the
  // Output tab and status badge both already read from that same state
  // with no changes needed.
  const runNodeMutation = useMutation({
    mutationFn: (nodeId: string) => protocolsApi.runNode(protocolId, nodeId),
    onSuccess: (run) => setRunId(run.id),
  })

  // First refetchInterval-based poll in this codebase -- no existing
  // long-running-job UI to mirror. Function form so polling stops itself
  // once the run reaches a terminal status, rather than polling forever.
  const runQuery = useQuery({
    queryKey: ['protocols', protocolId, 'runs', runId],
    queryFn: () => protocolsApi.getRun(protocolId, runId!),
    enabled: !!runId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status && TERMINAL_RUN_STATUSES.has(status) ? false : RUN_POLL_MS
    },
  })

  const isRunning = runMutation.isPending || (!!runQuery.data && !TERMINAL_RUN_STATUSES.has(runQuery.data.status))

  // A connected execution-pattern node must never be deletable directly
  // (Backspace/Delete key, NodeHoverToolbar's trash icon -- both go through
  // xyflow's own deleteElements, which skips any node with deletable:
  // false) -- an agent's pattern connector must never go to zero (see
  // AgentNode.tsx's own comment), so the only way to remove one is to
  // replace it via the connector's own "+" (addNode()'s pendingConnectorAdd
  // branch). The cap of one means being the source of ANY
  // architectural_pattern edge already identifies the sole pattern node
  // for its target agent -- no need to also check "exactly one" here.
  const nonDeletablePatternNodeIds = useMemo(
    () => new Set(edges.filter((e) => e.targetHandle === 'architectural_pattern').map((e) => e.source)),
    [edges],
  )

  // The one node-level "this can't run" condition an Agent card can flag
  // without a live check: no LLM wired at all (topological_order's own
  // "exactly one LLM connection" requirement). Every OTHER connector
  // mismatch topological_order checks for is already prevented at
  // wire-time by isValidConnection, and an execution pattern can never
  // reach zero (see nonDeletablePatternNodeIds above) -- so this is the
  // only one actually reachable through normal use.
  const agentIdsWithLlm = useMemo(() => new Set(edges.filter((e) => e.targetHandle === 'llm').map((e) => e.target)), [edges])

  // The canvas's per-node Play icon is only offered for a node with no
  // upstream *main* pipeline edge (mirrors services.protocol_execution's
  // _upstream_ids: any edge into this node whose targetHandle ISN'T one of
  // the typed connector slots) -- running a node mid-pipeline against real
  // upstream output needs a bounded/partial-run entrypoint this executor
  // doesn't have yet (see NodeHoverToolbar.tsx's own long-standing note on
  // n8n's "Execute step").
  const agentIdsWithUpstream = useMemo(
    () => new Set(edges.filter((e) => !CONNECTOR_HANDLES.has(e.targetHandle ?? '')).map((e) => e.target)),
    [edges],
  )

  const nodesWithRunStatus = useMemo((): Node[] => {
    return nodes.map((n) => ({
      ...n,
      deletable: !nonDeletablePatternNodeIds.has(n.id),
      data: {
        ...n.data,
        runStatus: runQuery.data?.node_runs[n.id]?.status,
        missingLlm: n.type === 'agent' && !agentIdsWithLlm.has(n.id),
        canRunAlone: n.type === 'agent' && !agentIdsWithUpstream.has(n.id),
      },
    }))
  }, [nodes, runQuery.data, nonDeletablePatternNodeIds, agentIdsWithLlm, agentIdsWithUpstream])

  // Same protection, one layer up -- the architectural_pattern EDGE itself
  // must not be removable on its own (InteractEdge never renders a hover
  // toolbar for one, so the only way a user can attempt this is selecting
  // the edge directly and pressing Backspace), or they could strand the
  // agent at zero patterns by removing just the edge without touching the
  // node. This has to be an onBeforeDelete veto rather than edge.deletable:
  // false the way the node-level guard above works -- deletable: false
  // would ALSO block xyflow's own cascade-removal of this same edge when
  // its AGENT is deleted (Backspace on the agent, or its hover-toolbar
  // trash icon), leaving a dangling edge that still points at the
  // now-gone agent -- which is exactly what stranded the orphaned pattern
  // node as permanently non-deletable/stuck showing "Swap" (nonDeletablePatternNodeIds
  // and useNodeConnections both still saw that stale edge). Here we can
  // tell the two cases apart: veto only when the edge's own target agent
  // isn't ALSO in this same delete batch.
  const onBeforeDelete = useCallback(
    async ({ nodes: deleting, edges: deletingEdges }: { nodes: Node[]; edges: Edge[] }) => {
      const deletedNodeIds = new Set(deleting.map((n) => n.id))
      const filteredEdges = deletingEdges.filter((e) => e.targetHandle !== 'architectural_pattern' || deletedNodeIds.has(e.target))
      // An edge-only deletion (no nodes -- e.g. selecting a single edge and
      // pressing Backspace) needs no confirmation, only removing a node
      // does -- matches the user-facing ask ("confirm before deleting
      // nodes") and keeps the low-friction edge-rewiring workflow intact.
      if (deleting.length === 0) {
        return { nodes: deleting, edges: filteredEdges }
      }
      return new Promise<boolean | { nodes: Node[]; edges: Edge[] }>((resolve) => {
        setPendingDelete({ nodes: deleting, edges: filteredEdges, resolve })
      })
    },
    [],
  )

  function closeAddPanel() {
    setAddPanelOpen(false)
    setPendingConnectorAdd(null)
    setPendingMainEdgeAdd(null)
    setPendingEdgeInsert(null)
  }

  // A connector "+" stub (ConnectorAddStub) requests this instead of
  // opening the unrestricted toolbar panel -- same panel, pre-filtered to
  // the slot's node type via CONNECTOR_PANEL_INFO, and addNode() below
  // wires the picked node straight into the requesting node's handle.
  const requestConnectorAdd = useCallback((request: ConnectorAddRequest) => {
    setSelectedNodeId(null)
    setPendingConnectorAdd(request)
    setAddPanelOpen(true)
  }, [])
  // A MainEdgeAddStub requests this instead -- same panel, restricted to
  // "agent" (the only node type this stub ever creates), and addNode()
  // below wires a plain edge (no handle id) in whichever direction the
  // requesting stub sits.
  const requestMainEdgeAdd = useCallback((request: MainEdgeAddRequest) => {
    setSelectedNodeId(null)
    setPendingMainEdgeAdd(request)
    setAddPanelOpen(true)
  }, [])
  // An InteractEdge's own "+" requests this -- same panel again, restricted
  // to "agent", and addNode() below removes the original edge and rewires
  // origin->newAgent->target instead.
  const requestEdgeInsert = useCallback((request: EdgeInsertRequest) => {
    setSelectedNodeId(null)
    setPendingEdgeInsert(request)
    setAddPanelOpen(true)
  }, [])
  // The canvas's per-node Play icon (NodeHoverToolbar) -- see
  // runNodeMutation's own comment for why this reuses the main Run
  // button's runId/runQuery polling state instead of a separate one.
  const requestRunNode = useCallback(
    (nodeId: string) => {
      setRunErrorDismissed(false)
      runNodeMutation.mutate(nodeId)
    },
    [runNodeMutation],
  )
  // The "Make experimental factor" icon inside Agent/Pattern's own inspector
  // title (next to the node's name -- see those inspectors' own title prop)
  // and Critic Gate's hover toolbar -- a no-op without a linked experiment,
  // since there's nothing to attach a factor to (matches FactorBindableField's
  // own disabled state for the same case). Deliberately does NOT clear
  // selectedNodeId: the Agent/Pattern title button is called from WITHIN an
  // already-open inspector for that same node, and closing it out from under
  // the user just to open the factor picker on top would be a worse
  // experience than the two dialogs simply stacking.
  const requestMakeFactor = useCallback(
    (nodeId: string) => {
      if (!experimentId) return
      setAddPanelOpen(false)
      setFactorPickerNodeId(nodeId)
    },
    [experimentId],
  )
  const canvasActions = useMemo(
    () => ({ requestConnectorAdd, requestMainEdgeAdd, requestEdgeInsert, requestRunNode, requestMakeFactor }),
    [requestConnectorAdd, requestMainEdgeAdd, requestEdgeInsert, requestRunNode, requestMakeFactor],
  )

  // Backing data for the per-node factor picker above -- fetched only while
  // the picker is actually open, same "fetch on demand" convention
  // FactorBindableField's own popover uses for the same query.
  const factorPickerExperimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId && !!factorPickerNodeId,
  })
  const factorPickerNode = nodes.find((n) => n.id === factorPickerNodeId) ?? null
  const factorPickerFields: UnboundField[] = factorPickerNodeId
    ? unboundBindableFields(nodes, edges).filter((f) => f.nodeId === factorPickerNodeId)
    : []
  const factorPickerExistingNames = factorPickerExperimentQuery.data?.design_spec?.factors?.map((f) => f.name) ?? []

  const createFactorMutation = useMutation({
    mutationFn: async ({ factor, field }: { factor: DesignFactor; field: UnboundField }) => {
      const fresh = factorPickerExperimentQuery.data ?? (await experimentsApi.get(experimentId!))
      const nextFactors = [...(fresh.design_spec?.factors ?? []), factor]
      await experimentsApi.update(experimentId!, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return { factor, field }
    },
    onSuccess: ({ factor, field }) => {
      bindFactorOnNode(field.nodeId, field.fieldPath, factor.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      setFactorPickerNodeId(null)
    },
  })

  // Every new Agent gets its own explicit default execution-pattern node
  // (agentic-core's own "reason_act" via _resolve_pattern_config) wired in
  // immediately -- delete it (or swap it for Single-Agent Baseline) to opt
  // out/change it. Shared by both add paths that create a bare Agent (the
  // unrestricted "+" toolbar and MainEdgeAddStub).
  function agentDefaultPattern(agentId: string, agentPosition: { x: number; y: number }, otherPositions: { x: number; y: number }[]) {
    const patternId = newNodeId()
    // Above the agent, not below -- its connector now sits on the agent's
    // OWN top edge (AgentNode.tsx), so the pattern node's source handle
    // faces down into it (CircleNode's own handlePosition="bottom" for this
    // node type) for a short, direct edge instead of one looping around the
    // whole card.
    const patternPosition = findFreePosition([...otherPositions, agentPosition], { x: agentPosition.x, y: agentPosition.y - 160 })
    const patternNode: Node = {
      id: patternId,
      type: 'pattern_reason_act',
      position: patternPosition,
      data: defaultReasonActPatternNodeData(),
    }
    const patternEdge: Edge = {
      id: newNodeId(),
      source: patternId,
      sourceHandle: 'architectural_pattern',
      target: agentId,
      targetHandle: 'architectural_pattern',
    }
    return { patternNode, patternEdge }
  }

  // n8n's own pattern: a "+" on the canvas opens a searchable node-type
  // panel on the right, rather than a static always-visible drag palette.
  // New nodes land near the pane's current center, nudged away from any
  // node already there (findFreePosition) so a fresh node never lands on
  // top of an existing one.
  function addNode(nodeType: string) {
    if (pendingConnectorAdd) {
      const { nodeId: originId, slot } = pendingConnectorAdd
      const originNode = nodes.find((n) => n.id === originId)
      // Architectural Pattern connects from above (its connector lives on
      // the agent's own TOP edge -- see AgentNode.tsx), every other slot
      // from below -- matches agentDefaultPattern's own placement, so a
      // swapped-in replacement pattern node lands in the same spot the
      // auto-created default one did.
      const desired = originNode
        ? { x: originNode.position.x, y: originNode.position.y + (slot === 'architectural_pattern' ? -160 : 160) }
        : screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
      const position = findFreePosition(nodes.map((n) => n.position), desired)
      const newId = newNodeId()
      // Execution pattern is capped at one but must never go to zero (see
      // AgentNode.tsx's own comment) -- its "+" stays visible even once
      // connected, and picking a node here always REPLACES whichever
      // pattern node is currently wired (removing it and its edge) rather
      // than adding a second, which protocol_execution.py's own "at most
      // one execution-pattern connection" validation would reject anyway.
      const existingPatternEdge =
        slot === 'architectural_pattern'
          ? edges.find((e) => e.target === originId && e.targetHandle === 'architectural_pattern')
          : undefined
      setNodes((nds) => nds.filter((n) => n.id !== existingPatternEdge?.source).concat({ id: newId, type: nodeType, position, data: defaultDataFor(nodeType) }))
      setEdges((eds) =>
        eds
          .filter((e) => e.id !== existingPatternEdge?.id)
          .concat({ id: newNodeId(), source: newId, sourceHandle: slot, target: originId, targetHandle: slot }),
      )
      setPendingConnectorAdd(null)
      setAddPanelOpen(false)
      // Mirrors n8n's own flow: picking a node from the connector panel
      // goes straight into that node's Inspector to set it up, rather than
      // leaving the user to double-click it themselves.
      setSelectedNodeId(newId)
      return
    }
    if (pendingMainEdgeAdd) {
      // Always an Agent -- AddNodePanel is restricted to ['agent'] for this
      // request (see the allowedTypes prop below), matching what
      // MainEdgeAddStub is for. Positioned left/right of the origin (main
      // flow is left-to-right) rather than below it, unlike a connector add.
      const { nodeId: originId, direction } = pendingMainEdgeAdd
      const originNode = nodes.find((n) => n.id === originId)
      const desired = originNode
        ? { x: originNode.position.x + (direction === 'outgoing' ? 340 : -340), y: originNode.position.y }
        : screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
      const position = findFreePosition(nodes.map((n) => n.position), desired)
      const newId = newNodeId()
      const newNode: Node = { id: newId, type: 'agent', position, data: defaultDataFor('agent') }
      const mainEdge: Edge =
        direction === 'outgoing'
          ? { id: newNodeId(), source: originId, target: newId }
          : { id: newNodeId(), source: newId, target: originId }
      const { patternNode, patternEdge } = agentDefaultPattern(newId, position, nodes.map((n) => n.position))
      setNodes((nds) => nds.concat(newNode, patternNode))
      setEdges((eds) => eds.concat(mainEdge, patternEdge))
      setPendingMainEdgeAdd(null)
      setAddPanelOpen(false)
      setSelectedNodeId(newId)
      return
    }
    if (pendingEdgeInsert) {
      // Splits the original edge into origin->newAgent->target -- always an
      // Agent (AddNodePanel restricted to ['agent'] below), positioned at
      // the midpoint of the two nodes the removed edge used to connect.
      const { edgeId, source, target } = pendingEdgeInsert
      const sourceNode = nodes.find((n) => n.id === source)
      const targetNode = nodes.find((n) => n.id === target)
      const desired =
        sourceNode && targetNode
          ? { x: (sourceNode.position.x + targetNode.position.x) / 2, y: (sourceNode.position.y + targetNode.position.y) / 2 }
          : screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
      const position = findFreePosition(nodes.map((n) => n.position), desired)
      const newId = newNodeId()
      const newNode: Node = { id: newId, type: 'agent', position, data: defaultDataFor('agent') }
      const { patternNode, patternEdge } = agentDefaultPattern(newId, position, nodes.map((n) => n.position))
      setNodes((nds) => nds.concat(newNode, patternNode))
      setEdges((eds) =>
        eds
          .filter((e) => e.id !== edgeId)
          .concat(
            { id: newNodeId(), source, target: newId },
            { id: newNodeId(), source: newId, target },
            patternEdge,
          ),
      )
      setPendingEdgeInsert(null)
      setAddPanelOpen(false)
      setSelectedNodeId(newId)
      return
    }
    const rect = paneRef.current?.getBoundingClientRect()
    const center = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    const desired = screenToFlowPosition(center)
    const position = findFreePosition(nodes.map((n) => n.position), desired)
    const newId = newNodeId()
    const newNode: Node = { id: newId, type: nodeType, position, data: defaultDataFor(nodeType) }

    if (nodeType === 'agent') {
      // Unlike LLM (no auto-created default; you must wire one), every new
      // Agent gets an explicit default pattern immediately -- see
      // agentDefaultPattern's own comment.
      const { patternNode, patternEdge } = agentDefaultPattern(newId, position, nodes.map((n) => n.position))
      setNodes((nds) => nds.concat(newNode, patternNode))
      setEdges((eds) => eds.concat(patternEdge))
      setAddPanelOpen(false)
      return
    }

    setNodes((nds) => nds.concat(newNode))
    setAddPanelOpen(false)
  }

  // Debounced autosave: every nodes/edges change schedules a PATCH, reset on
  // the next change -- so a node drag (many onNodesChange firings) or a
  // burst of inspector edits only ever produces one write, 800ms after the
  // user stops.
  useEffect(() => {
    const graph = toPersistedGraph(nodes, edges)
    const timer = setTimeout(() => {
      protocolsApi.update(protocolId, { graph }).catch(() => {
        // Best-effort autosave; a transient failure just means the next
        // change's save attempt will carry the current (still-correct)
        // in-memory state forward.
      })
    }, AUTOSAVE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [nodes, edges, protocolId])

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null
  // Computed once per selection change, not per FactorBindableField -- an
  // LLM/Tool/Memory node's plain label alone doesn't say which agent it
  // belongs to (see bindableFields.ts's own comment), so every inspector
  // that wraps a field in "+ Make experimental factor" gets this instead of
  // data.label for that purpose specifically; the header title itself still
  // shows the node's own plain label, unaffected.
  const factorNodeLabel = selectedNode ? agentTracedLabel(selectedNode, edges, nodes) : ''

  // The mirror image of FactorsEditor's own delete/rename cleanup
  // (DesignTab.tsx): removing a factor there sweeps every node's
  // factor_bindings via removeFactorBindings/renameFactorBindings, but
  // nothing swept the OTHER direction -- unbinding a field from its factor
  // in a node's own inspector (the "Factor: {name}" badge's X) only ever
  // cleared that one node's own factor_bindings entry, leaving the factor
  // itself sitting in design_spec.factors forever with nothing left to bind
  // it to, invisible until the user separately opened the Design tab and
  // deleted it there by hand. Only prunes a factor once NO node/field
  // anywhere still references it -- a factor deliberately shared across
  // several nodes (e.g. spinal-use-case.json's "Critic enabled" spanning
  // all 4 Critic Gate nodes) survives unbinding just one of them.
  async function pruneOrphanedFactors(nextNodes: Node[], removedFactorNames: string[]) {
    if (!experimentId || removedFactorNames.length === 0) return
    const stillReferenced = new Set<string>()
    for (const n of nextNodes) {
      const bindings = (n.data as { factor_bindings?: Record<string, string> } | undefined)?.factor_bindings ?? {}
      for (const name of Object.values(bindings)) stillReferenced.add(name)
    }
    const orphaned = removedFactorNames.filter((name) => !stillReferenced.has(name))
    if (orphaned.length === 0) return
    const fresh = await experimentsApi.get(experimentId)
    const existingFactors = fresh.design_spec?.factors ?? []
    const nextFactors = existingFactors.filter((f) => !orphaned.includes(f.name))
    if (nextFactors.length === existingFactors.length) return
    await experimentsApi.update(experimentId, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
    queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
  }

  function updateNodeData(
    nodeId: string,
    data:
      | AgentNodeData
      | McpToolNodeData
      | CriticGateNodeData
      | LlmNodeData
      | MemoryNodeData
      | DatasetNodeData
      | ScriptNodeData
      | ReasonActPatternNodeData
      | SingleAgentBaselinePatternNodeData,
  ) {
    const oldBindings =
      (nodes.find((n) => n.id === nodeId)?.data as { factor_bindings?: Record<string, string> } | undefined)
        ?.factor_bindings ?? {}
    const newBindings = (data as { factor_bindings?: Record<string, string> }).factor_bindings ?? {}
    const removedFactorNames = Object.entries(oldBindings)
      .filter(([path, name]) => newBindings[path] !== name)
      .map(([, name]) => name)
    const nextNodes = nodes.map((n) => (n.id === nodeId ? { ...n, data } : n))
    setNodes(nextNodes)
    if (removedFactorNames.length > 0) void pruneOrphanedFactors(nextNodes, removedFactorNames)
  }

  // Client-side guardrail mirroring the backend's own connector validation
  // (topological_order in services/protocol_execution.py is the real source
  // of truth) -- an invalid drag never even completes, rather than
  // completing and only failing later at Run time.
  const isValidConnection = useCallback(
    (connection: Edge | Connection) => {
      const sourceNode = nodes.find((n) => n.id === connection.source)
      const targetNode = nodes.find((n) => n.id === connection.target)
      if (!sourceNode || !targetNode) return false
      switch (connection.targetHandle) {
        case 'llm':
          return (
            LLM_NODE_TYPES.includes(sourceNode.type ?? '') &&
            (targetNode.type === 'agent' || targetNode.type === 'critic_gate')
          )
        case 'tool':
          return (
            (sourceNode.type === 'mcp_tool' || sourceNode.type === 'dataset' || sourceNode.type === 'script') &&
            targetNode.type === 'agent'
          )
        case 'memory':
          return sourceNode.type === 'memory' && targetNode.type === 'agent'
        case 'architectural_pattern':
          return PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') && targetNode.type === 'agent'
        default:
          // A plain "main" pipeline edge -- LLM/memory/pattern/mcp_tool/
          // dataset/script nodes have no main handle to drag from in the
          // first place, so this mostly guards against a stray connection,
          // not real interactive use.
          return (
            !LLM_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'memory' &&
            sourceNode.type !== 'mcp_tool' &&
            sourceNode.type !== 'dataset' &&
            sourceNode.type !== 'script' &&
            !PATTERN_NODE_TYPES.includes(sourceNode.type ?? '')
          )
      }
    },
    [nodes],
  )

  function deleteNode(nodeId: string) {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId))
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId))
    setSelectedNodeId(null)
  }

  // The node inspector's own Delete button calls deleteNode directly --
  // never through xyflow's deleteElements, so onBeforeDelete never sees it.
  // Shows the same confirmation dialog either way (no `resolve`, since
  // there's no pending xyflow deletion to approve/reject here -- just call
  // deleteNode for real once the user confirms).
  function requestDeleteNode(nodeId: string) {
    const node = nodes.find((n) => n.id === nodeId)
    if (!node) return
    const relatedEdges = edges.filter((e) => e.source === nodeId || e.target === nodeId)
    setPendingDelete({ nodes: [node], edges: relatedEdges })
  }

  // "Import from file..." (ProtocolCanvasMenu) hands back the parsed
  // {nodes, edges} -- merges them in ALONGSIDE the current canvas (not a
  // replace, per the user's own read of n8n's import behavior): every
  // imported node gets a fresh id (newNodeId is collision-safe by
  // construction) and every edge's source/target is rewritten to match;
  // the whole imported cluster is translated so its bounding-box center
  // lands near the current viewport center (same placement logic addNode
  // already uses), preserving the imported nodes' relative spacing to each
  // other, then each translated position runs through the existing
  // findFreePosition against both the current canvas's nodes AND whichever
  // imported nodes have already been placed this same import -- so nothing
  // lands exactly on top of an existing OR a freshly-imported node.
  function handleImport(imported: ProtocolGraph) {
    if (imported.nodes.length === 0) return
    const idMap = new Map(imported.nodes.map((n) => [n.id, newNodeId()]))

    const rect = paneRef.current?.getBoundingClientRect()
    const center = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    const desiredCenter = screenToFlowPosition(center)

    const xs = imported.nodes.map((n) => n.position.x)
    const ys = imported.nodes.map((n) => n.position.y)
    const bboxCenter = { x: (Math.min(...xs) + Math.max(...xs)) / 2, y: (Math.min(...ys) + Math.max(...ys)) / 2 }
    const offset = { x: desiredCenter.x - bboxCenter.x, y: desiredCenter.y - bboxCenter.y }

    const placed: Node['position'][] = []
    const newNodes: Node[] = imported.nodes.map((n) => {
      const translated = { x: n.position.x + offset.x, y: n.position.y + offset.y }
      const position = findFreePosition([...nodes.map((existing) => existing.position), ...placed], translated)
      placed.push(position)
      return { id: idMap.get(n.id)!, type: n.type, position, data: n.data }
    })
    const newEdges: Edge[] = imported.edges
      .filter((e) => idMap.has(e.source) && idMap.has(e.target))
      .map((e) => ({
        id: newNodeId(),
        source: idMap.get(e.source)!,
        target: idMap.get(e.target)!,
        sourceHandle: e.sourceHandle,
        targetHandle: e.targetHandle,
      }))

    setNodes((nds) => nds.concat(newNodes))
    setEdges((eds) => eds.concat(newEdges))
  }

  return (
    <ProtocolCanvasActionsProvider value={canvasActions}>
      <div className="flex h-full w-full">
        <div ref={paneRef} className="relative flex-1">
          <ReactFlow
            nodes={nodesWithRunStatus}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            isValidConnection={isValidConnection}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            onBeforeDelete={onBeforeDelete}
            onNodeClick={() => setAddPanelOpen(false)}
            onNodeDoubleClick={(_, node) => {
              setAddPanelOpen(false)
              setSelectedNodeId(node.id)
            }}
            onPaneClick={() => setSelectedNodeId(null)}
            onNodesDelete={(deleted) => {
              if (deleted.some((n) => n.id === selectedNodeId)) setSelectedNodeId(null)
            }}
            fitView
            fitViewOptions={{ maxZoom: DEFAULT_ZOOM }}
            defaultViewport={{ x: 0, y: 0, zoom: DEFAULT_ZOOM }}
            minZoom={0.2}
            // xyflow's default is zoomOnScroll -- a two-finger trackpad
            // scroll and a real pinch are both plain wheel events, and
            // without panOnScroll xyflow can't tell them apart, so scrolling
            // zoomed instead of panning. Flipping this pair (matching n8n/
            // Figma/Miro) makes two-finger scroll pan; pinch still zooms,
            // since xyflow's own wheel handler checks the event's ctrlKey
            // (a real pinch gesture sets it, a plain scroll doesn't).
            panOnScroll
            zoomOnScroll={false}
            proOptions={{ hideAttribution: true }}
            onInit={onCanvasInit}
          >
            <Background color="var(--primary)" gap={28} size={1} style={{ opacity: 0.2 }} />
            {!isAtRest && (
              <MiniMap pannable zoomable className="!bg-card" maskColor="color-mix(in oklch, var(--background), transparent 40%)" />
            )}
          </ReactFlow>
          <CanvasControls />
          {(() => {
            // runMutation.error/runNodeMutation.error is the real validation
            // message (e.g. topological_order/validate_single_node_runnable
            // rejecting before any ProtocolRun row even exists) --
            // runQuery.data?.error only ever exists once a run row was
            // created and later failed asynchronously in the worker.
            const failedMutation = runMutation.isError ? runMutation : runNodeMutation.isError ? runNodeMutation : null
            const runErrorText =
              runQuery.data?.error ??
              (failedMutation
                ? failedMutation.error instanceof ApiError && typeof failedMutation.error.detail === 'string'
                  ? failedMutation.error.detail
                  : 'Could not start the run.'
                : null)
            if (!runErrorText || runErrorDismissed) return null
            return (
              // A full-text, wrapping, dismissible banner -- n8n's own
              // convention for a run failure (a toast with the complete
              // message and a close button) rather than this app's usual
              // single-line truncate+title-tooltip idiom, which hides
              // exactly the detail (e.g. "No anthropic credential
              // configured...") a failed run needs to actually show.
              <div
                role="alert"
                className="absolute top-14 right-3 z-10 flex max-w-sm items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive shadow-[0_0_16px_-6px_var(--destructive)]"
              >
                <span className="whitespace-pre-wrap">{runErrorText}</span>
                <button
                  type="button"
                  aria-label="Dismiss"
                  onClick={() => setRunErrorDismissed(true)}
                  className="-mt-0.5 -mr-1 shrink-0 cursor-pointer rounded p-0.5 text-destructive/70 hover:bg-destructive/20 hover:text-destructive"
                >
                  <X className="size-3.5" />
                </button>
              </div>
            )
          })()}
          <div className="absolute top-3 right-3 z-10 flex items-center gap-2">
            <Button size="sm" disabled={isRunning} onClick={() => { setRunErrorDismissed(false); runMutation.mutate() }}>
              <Play className="size-4" />
              {isRunning ? 'Running…' : 'Run'}
            </Button>
            <Button
              size="icon"
              className="rounded-full"
              aria-label="Add node"
              onClick={() => {
                setSelectedNodeId(null)
                setPendingConnectorAdd(null)
                setAddPanelOpen(true)
              }}
            >
              <Plus className="size-4" />
            </Button>
            <ProtocolCanvasMenu
              protocolId={protocolId}
              experimentId={experimentId}
              nodes={nodes}
              edges={edges}
              onImport={handleImport}
            />
          </div>
        </div>
        {addPanelOpen ? (
          <AddNodePanel
            onAdd={addNode}
            onClose={closeAddPanel}
            allowedTypes={
              pendingConnectorAdd
                ? CONNECTOR_PANEL_INFO[pendingConnectorAdd.slot].allowedTypes
                : pendingMainEdgeAdd || pendingEdgeInsert
                  ? ['agent']
                  : undefined
            }
            title={
              pendingConnectorAdd
                ? CONNECTOR_PANEL_INFO[pendingConnectorAdd.slot].title
                : pendingMainEdgeAdd
                  ? 'Connect an agent'
                  : pendingEdgeInsert
                    ? 'Insert an agent'
                    : undefined
            }
          />
        ) : selectedNode?.type === 'mcp_tool' ? (
          <McpToolNodeInspector
            node={{ id: selectedNode.id, type: 'mcp_tool', position: selectedNode.position, data: selectedNode.data as McpToolNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'critic_gate' ? (
          <CriticGateNodeInspector
            node={{ id: selectedNode.id, type: 'critic_gate', position: selectedNode.position, data: selectedNode.data as CriticGateNodeData }}
            experimentId={experimentId}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : LLM_NODE_TYPES.includes(selectedNode?.type ?? '') ? (
          <LlmNodeInspector
            node={{ id: selectedNode!.id, type: selectedNode!.type!, position: selectedNode!.position, data: selectedNode!.data as LlmNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'memory' ? (
          <MemoryNodeInspector
            node={{ id: selectedNode.id, type: 'memory', position: selectedNode.position, data: selectedNode.data as MemoryNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'dataset' ? (
          <DatasetNodeInspector
            node={{ id: selectedNode.id, type: 'dataset', position: selectedNode.position, data: selectedNode.data as DatasetNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'script' ? (
          <ScriptNodeInspector
            node={{ id: selectedNode.id, type: 'script', position: selectedNode.position, data: selectedNode.data as ScriptNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'pattern_reason_act' ? (
          <ReasonActPatternNodeInspector
            node={{
              id: selectedNode.id,
              type: 'pattern_reason_act',
              position: selectedNode.position,
              data: selectedNode.data as ReasonActPatternNodeData,
            }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'pattern_single_agent_baseline' ? (
          <SingleAgentBaselinePatternNodeInspector
            node={{
              id: selectedNode.id,
              type: 'pattern_single_agent_baseline',
              position: selectedNode.position,
              data: selectedNode.data as SingleAgentBaselinePatternNodeData,
            }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : (
          selectedNode && (
            <AgentNodeInspector
              node={{ id: selectedNode.id, type: selectedNode.type ?? 'agent', position: selectedNode.position, data: selectedNode.data as AgentNodeData }}
              experimentId={experimentId}
              nodeRun={runQuery.data?.node_runs[selectedNode.id]}
              onChange={updateNodeData}
              onDelete={requestDeleteNode}
              onClose={() => setSelectedNodeId(null)}
            />
          )
        )}
      </div>
      {pendingDelete && (
        <DeleteNodeConfirmDialog
          nodes={pendingDelete.nodes}
          onCancel={() => {
            pendingDelete.resolve?.(false)
            setPendingDelete(null)
          }}
          onConfirm={() => {
            if (pendingDelete.resolve) {
              pendingDelete.resolve({ nodes: pendingDelete.nodes, edges: pendingDelete.edges })
            } else {
              // requestDeleteNode's path -- no xyflow deletion pending, just
              // remove the node for real now that the user confirmed.
              deleteNode(pendingDelete.nodes[0].id)
            }
            setPendingDelete(null)
          }}
        />
      )}
      {factorPickerNodeId && (
        <FactorEditorDialog
          open
          onOpenChange={(open) => {
            if (!open) setFactorPickerNodeId(null)
          }}
          factor={{ name: '', levels: [], level_type: 'string' }}
          pickableFields={factorPickerFields}
          existingNames={factorPickerExistingNames}
          emptyPickerMessage={`${(factorPickerNode?.data as { label?: string })?.label || 'This node'} has no fields that can be turned into a factor.`}
          onSave={(factor, field) => {
            if (field) createFactorMutation.mutate({ factor, field })
          }}
        />
      )}
    </ProtocolCanvasActionsProvider>
  )
})
