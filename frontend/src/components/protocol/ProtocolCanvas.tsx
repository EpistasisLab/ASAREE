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
import { Play, Plus, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { newNodeId } from '@/lib/nodeId'
import { protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import { TERMINAL_RUN_STATUSES } from '@/lib/protocolRun'
import {
  defaultAgentNodeData,
  defaultAnthropicLlmNodeData,
  defaultAzureFoundryLlmNodeData,
  defaultCriticGateNodeData,
  defaultDatasetNodeData,
  defaultLocalLlmNodeData,
  defaultMcpToolNodeData,
  defaultMemoryNodeData,
  defaultOpenAiLlmNodeData,
  defaultOpenRouterLlmNodeData,
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
  OkfBundleNodeData,
  OkfDocumentNodeData,
  ProtocolGraph,
  ProtocolNode,
  ReasonActPatternNodeData,
  ScriptNodeData,
  SingleAgentBaselinePatternNodeData,
  SkillNodeData,
} from '@/types/protocols'
import type { Dataset } from '@/types/datasets'
import type { DesignFactor } from '@/types/experiments'
import type { McpServer } from '@/types/mcpServers'
import type { OkfBundle, OkfDocument } from '@/types/okf'
import type { Skill } from '@/types/skills'
import { AddNodePanel } from './AddNodePanel'
import { AgentNodeInspector } from './AgentNodeInspector'
import { agentTracedLabel, revealsHiddenMcpServers, unboundBindableFields, type UnboundField } from './bindableFields'
import { CanvasControls } from './CanvasControls'
import { CriticGateNodeInspector } from './CriticGateNodeInspector'
import { DatasetNodeInspector } from './DatasetNodeInspector'
import { DeleteNodeConfirmDialog } from './DeleteNodeConfirmDialog'
import { DEFAULT_ZOOM } from './constants'
import { FactorEditorDialog } from './FactorEditorDialog'
import { CONNECTOR_CHILD_CLEARANCE, connectorNodeOffsetX, findFreePosition, tidyLayout } from './layout'
import { LlmNodeInspector } from './LlmNodeInspector'
import { DatasetBrowserPanel } from './DatasetBrowserPanel'
import { DATASET_BROWSE, nodeDataForDataset } from './datasetCatalog'
import { McpServerBrowserPanel } from './McpServerBrowserPanel'
import {
  MCP_CLIENT_TOOL_NODE_TYPE,
  MCP_SERVER_BROWSE,
  MCP_TOOL_NODE_TYPES,
  nodeDataForClientTool,
  nodeDataForServer,
  presetForServer,
} from './mcpServerCatalog'
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
import { RunConfirmDialog } from './RunConfirmDialog'
import type { RunScope } from './runSummary'
import { ScriptNodeInspector } from './ScriptNodeInspector'
import { SelectCellDialog } from './SelectCellDialog'
import { SingleAgentBaselinePatternNodeInspector } from './SingleAgentBaselinePatternNodeInspector'
import { OkfBundleBrowserPanel } from './OkfBundleBrowserPanel'
import { OKF_BUNDLE_BROWSE, OKF_DOCUMENT_BROWSE, nodeDataForBundle, nodeDataForDocument } from './okfCatalog'
import { OkfBundleNodeInspector } from './OkfBundleNodeInspector'
import { OkfDocumentBrowserPanel } from './OkfDocumentBrowserPanel'
import { OkfDocumentNodeInspector } from './OkfDocumentNodeInspector'
import { SkillBrowserPanel } from './SkillBrowserPanel'
import { SKILL_BROWSE, nodeDataForSkill } from './skillCatalog'
import { SkillNodeInspector } from './SkillNodeInspector'
import { InteractEdge } from './edges/InteractEdge'
import { AgentNode } from './nodes/AgentNode'
import { CriticGateNode } from './nodes/CriticGateNode'
import { DatasetNode } from './nodes/DatasetNode'
import { LlmNode } from './nodes/LlmNode'
import { McpClientToolNode } from './nodes/McpClientToolNode'
import { McpToolNode } from './nodes/McpToolNode'
import { MemoryNode } from './nodes/MemoryNode'
import { ReasonActPatternNode } from './nodes/ReasonActPatternNode'
import { ScriptNode } from './nodes/ScriptNode'
import { SingleAgentBaselinePatternNode } from './nodes/SingleAgentBaselinePatternNode'
import { OkfBundleNode } from './nodes/OkfBundleNode'
import { OkfDocumentNode } from './nodes/OkfDocumentNode'
import { SkillNode } from './nodes/SkillNode'
import { ProtocolCanvasMenu } from './ProtocolCanvasMenu'

// One node type per LLM provider / architectural pattern (see LlmNodeData/
// ReasonActPatternNodeData's own comments in types/protocols.ts) -- each
// connector slot accepts this whole family, not one exact type, mirroring
// how the "tool" slot already accepts any mcp_tool node.
const LLM_NODE_TYPES = ['llm_anthropic', 'llm_openai', 'llm_azure_foundry', 'llm_openrouter', 'llm_local']
const PATTERN_NODE_TYPES = ['pattern_reason_act', 'pattern_single_agent_baseline']
// The Knowledge slot's family, mirroring _KNOWLEDGE_NODE_TYPES in
// services/protocol_execution.py: a server-side folder or an uploaded single
// concept, both resolved identically into the agent's tool allow-list.
const KNOWLEDGE_NODE_TYPES = ['okf_bundle', 'okf_document']
// Mirrors services.protocol_execution's own _CONNECTOR_HANDLES -- any edge
// whose targetHandle ISN'T one of these is a plain "main" pipeline edge.
// Includes the pre-rename "llm" and "resource" spellings for the same reason
// the backend set does: a graph that hasn't been through migrateLegacyHandles
// yet must not have its AI/Dataset edges misread as main pipeline edges.
const CONNECTOR_HANDLES = new Set([
  'ai',
  'llm',
  'tool',
  'memory',
  'architectural_pattern',
  'skill',
  'dataset',
  'resource',
  'knowledge',
])
// The four connector slots that live on an Agent's TOP edge (see
// AgentNode.tsx) -- a node feeding one of these is placed ABOVE its agent,
// every other slot's source below it.
const TOP_EDGE_SLOTS = new Set<ConnectorSlot>(['architectural_pattern', 'skill', 'dataset', 'knowledge'])

const NODE_TYPES = {
  agent: AgentNode,
  // Both MCP-tool types render through the same component (as the five LLM
  // provider types do) -- they carry identical data and differ only in
  // whether their server was picked in the browser or in a dropdown.
  mcp_tool: McpToolNode,
  mcp_scikit_learn: McpToolNode,
  // The exception to "both MCP-tool types render the same": a client tool has
  // its own icon and hue, since where its server came from is the one thing
  // that distinguishes it. Same data, same inspector.
  mcp_client_tool: McpClientToolNode,
  critic_gate: CriticGateNode,
  // All five LLM provider types render through the same component -- it
  // derives icon/accent/placeholder from data.config.provider, not from
  // which of these five keys it was registered under.
  llm_anthropic: LlmNode,
  llm_openai: LlmNode,
  llm_azure_foundry: LlmNode,
  llm_openrouter: LlmNode,
  llm_local: LlmNode,
  memory: MemoryNode,
  dataset: DatasetNode,
  skill: SkillNode,
  okf_bundle: OkfBundleNode,
  okf_document: OkfDocumentNode,
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
  // mcp_scikit_learn, mcp_client_tool, skill, okf_bundle and okf_document
  // aren't here: none is ever created blank -- addServerNode/
  // addClientToolNode/addSkillNode/addBundleNode/addDocumentNode build the
  // data from the picked or just-connected server/skill/bundle/document, since
  // a node whose whole identity is one of those would be meaningless without
  // it.
  if (nodeType === 'mcp_tool') return defaultMcpToolNodeData()
  if (nodeType === 'critic_gate') return defaultCriticGateNodeData()
  if (nodeType === 'llm_anthropic') return defaultAnthropicLlmNodeData()
  if (nodeType === 'llm_openai') return defaultOpenAiLlmNodeData()
  if (nodeType === 'llm_azure_foundry') return defaultAzureFoundryLlmNodeData()
  if (nodeType === 'llm_openrouter') return defaultOpenRouterLlmNodeData()
  if (nodeType === 'llm_local') return defaultLocalLlmNodeData()
  if (nodeType === 'memory') return defaultMemoryNodeData()
  if (nodeType === 'dataset') return defaultDatasetNodeData()
  if (nodeType === 'script') return defaultScriptNodeData()
  if (nodeType === 'pattern_reason_act') return defaultReasonActPatternNodeData()
  if (nodeType === 'pattern_single_agent_baseline') return defaultSingleAgentBaselinePatternNodeData()
  return defaultAgentNodeData()
}

// The registered datasets this canvas declares, in the order their nodes were
// added -- what gets PATCHed onto the experiment's own dataset list (see the
// syncExperimentDatasets effect). Nodes still on the browse placeholder have no
// dataset_id yet and are skipped; two nodes naming the same dataset collapse to
// one entry, since the join table is keyed by (experiment, dataset).
//
// `factors` is the experiment's own design_spec.factors, and it matters
// because a Dataset node whose whole `config` is bound to a 'dataset_config'
// factor holds only ONE of the datasets this experiment runs against -- the
// base level sitting in the node. The rest live in that factor's levels, and
// apply_factor_bindings substitutes them per cell at run time, so reading the
// graph alone would under-report the experiment's data to everything that
// asks the record instead of the canvas. Levels come after the node's own id
// (position on the join row is canvas wiring order), and duplicates collapse
// the same way.
// Module-level so the dataset-sync effect's dependency list gets a stable
// reference when the experiment has no factors -- a fresh [] every render
// would re-run it forever.
const EMPTY_FACTORS: DesignFactor[] = []

function datasetIdsInGraph(nodes: Node[], factors: DesignFactor[] = EMPTY_FACTORS): string[] {
  const ids: string[] = []
  const add = (id: unknown) => {
    if (typeof id === 'string' && id && !ids.includes(id)) ids.push(id)
  }
  const boundFactorNames = new Set<string>()
  for (const node of nodes) {
    if (node.type !== 'dataset') continue
    const data = node.data as DatasetNodeData
    add(data.config?.dataset_id)
    const factorName = data.factor_bindings?.config
    if (factorName) boundFactorNames.add(factorName)
  }
  for (const factor of factors) {
    if (factor.level_type !== 'dataset_config' || !boundFactorNames.has(factor.name)) continue
    for (const level of factor.levels) add((level as { dataset_id?: unknown } | null)?.dataset_id)
  }
  return ids
}

// Mirrors isValidConnection's own per-slot source-type-family rule -- the
// panel that opens for a connector "+" is pre-filtered to that slot's whole
// family of node types (LLM_NODE_TYPES/PATTERN_NODE_TYPES above) rather than
// the full catalog. Tool's own family includes Script alongside mcp_tool
// (one connector accepting several kinds of node -- see AgentNode.tsx's own
// comment on its Tool handle): a Script is a pure config source with no
// callable capability of its own, so it shares Tool's slot rather than
// getting a dedicated one. Dataset used to share it too, but now has its
// own slot -- what an agent operates ON, not a capability it operates WITH.
const CONNECTOR_PANEL_INFO: Record<ConnectorSlot, { allowedTypes: string[]; title: string }> = {
  ai: { allowedTypes: LLM_NODE_TYPES, title: 'Add AI' },
  tool: { allowedTypes: [MCP_SERVER_BROWSE, 'script'], title: 'Add Tool' },
  memory: { allowedTypes: ['memory'], title: 'Add Memory' },
  architectural_pattern: { allowedTypes: PATTERN_NODE_TYPES, title: 'Add Architectural Pattern' },
  skill: { allowedTypes: [SKILL_BROWSE], title: 'Add Skill' },
  dataset: { allowedTypes: [DATASET_BROWSE], title: 'Add Dataset' },
  // The one slot with two entries in its panel: knowledge arrives either as a
  // folder already on the server (bundle) or as a file the user uploads
  // (document), and which of those you have is the question the panel asks.
  knowledge: { allowedTypes: [OKF_BUNDLE_BROWSE, OKF_DOCUMENT_BROWSE], title: 'Add Knowledge' },
}

// Connector slots have been renamed since graphs started being saved, and a
// slot id lives in persisted data (it's the edge's source/targetHandle):
//
//   * "llm" -> "ai", on every edge, when the connector's caption became "AI".
//   * "tool" -> "resource" for DATASET-sourced edges only -- Dataset used to
//     share the Tool slot with mcp_tool/script, which both keep "tool". Hence
//     the source-type check rather than a blanket swap.
//   * "resource" -> "dataset", on every edge -- that slot's only member is the
//     Dataset node, so it was renamed after it (and moved next to Skill). No
//     source-type check needed: nothing else has ever used "resource".
//
// Rewritten the moment a graph is loaded into the canvas so the edge lands on
// the right handle and the next autosave persists the fix. This is one of
// three layers, none of them load-bearing alone: Alembic data migrations
// (3f1a7c9b2e04, b7c2d9e14a35) make stored graphs canonical, the backend keeps
// resolving the old spellings (_LEGACY_AI_HANDLES / _LEGACY_DATASET_HANDLES in
// services/protocol_execution.py) so a graph that's never opened still runs,
// and this covers a tab that loaded before the deploy and is still autosaving
// old-spelling edges.
function migrateLegacyHandles(graph: ProtocolGraph): Edge[] {
  const datasetIds = new Set(graph.nodes.filter((n) => n.type === 'dataset').map((n) => n.id))
  return (graph.edges as Edge[]).map((e) => {
    if (e.targetHandle === 'llm') return { ...e, sourceHandle: 'ai', targetHandle: 'ai' }
    if (e.targetHandle === 'resource' || (e.targetHandle === 'tool' && datasetIds.has(e.source))) {
      return { ...e, sourceHandle: 'dataset', targetHandle: 'dataset' }
    }
    return e
  })
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
  hasUnpublishedChanges: boolean
  publishedRevision: number | null
}>(function ProtocolCanvas({ protocolId, experimentId, initialGraph, hasUnpublishedChanges, publishedRevision }, canvasHandleRef) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialGraph.nodes as Node[])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(migrateLegacyHandles(initialGraph))
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
  // The second level of the add-node panel: AddNodePanel's "MCP Servers"
  // entry swaps the browser in over it, and its Back button returns. Only
  // meaningful while addPanelOpen.
  const [serverBrowserOpen, setServerBrowserOpen] = useState(false)
  const [skillBrowserOpen, setSkillBrowserOpen] = useState(false)
  const [bundleBrowserOpen, setBundleBrowserOpen] = useState(false)
  const [documentBrowserOpen, setDocumentBrowserOpen] = useState(false)
  const [datasetBrowserOpen, setDatasetBrowserOpen] = useState(false)
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
  // Dismissible error banner (own close button, no auto-hide) --
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
  // Populated by the main Run button (whole graph or a picked cell) and by
  // each node's own per-node Play icon (requestRunNode below) -- opens
  // RunConfirmDialog instead of firing the run immediately, so a real
  // (billable) run never fires without the user seeing what will actually
  // execute first.
  const [pendingRunConfirm, setPendingRunConfirm] = useState<RunScope | null>(null)
  const [runId, setRunId] = useState<string | null>(null)
  // null -- today's ad-hoc, un-substituted whole-graph run (the only option
  // before any cells exist). Set -- runs that one already-generated cell for
  // real, its own factor_values substituted in (see
  // services.protocol_execution.plan_single_cell_run), picked from the
  // dropdown next to the Run button.
  const [selectedCellLabel, setSelectedCellLabel] = useState<string | null>(null)
  const [cellPickerOpen, setCellPickerOpen] = useState(false)
  const cellsQuery = useQuery({
    queryKey: ['experiments', experimentId, 'cells'],
    queryFn: () => experimentsApi.listCells(experimentId!),
    enabled: !!experimentId,
  })
  const designImpactQuery = useQuery({
    queryKey: ['experiments', experimentId, 'design-impact'],
    queryFn: () => experimentsApi.getDesignImpact(experimentId!),
    enabled: !!experimentId,
  })
  const cellOptions = cellsQuery.data ?? []
  const designRegenerationRequired = designImpactQuery.data?.regeneration_required ?? false
  // design_spec for SelectCellDialog's own factor-checkbox filter -- fetched
  // only while that dialog is actually open, same "on demand" convention
  // factorPickerExperimentQuery below uses for the same query.
  const cellPickerExperimentQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId && cellPickerOpen,
  })
  const paneRef = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition, fitView } = useReactFlow()

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

  // "Tidy up" -- reposition every node into a generated layout (see
  // layout.ts's tidyLayout). Goes through this component's own setNodes
  // rather than useReactFlow().setNodes because the flow is controlled, and
  // that's also what lets the existing debounced autosave pick the new
  // positions up with no extra request wiring. fitView waits a frame so it
  // measures the moved nodes, not the ones they replaced.
  const tidyUp = useCallback(() => {
    setNodes((nds) => {
      const positions = tidyLayout(nds, edges)
      return nds.map((n) => {
        const position = positions.get(n.id)
        return position ? { ...n, position } : n
      })
    })
    requestAnimationFrame(() => fitView({ maxZoom: DEFAULT_ZOOM, duration: 300 }))
  }, [edges, fitView, setNodes])

  const runMutation = useMutation({
    mutationFn: () => protocolsApi.run(protocolId, selectedCellLabel),
    onSuccess: (run) => setRunId(run.id),
  })

  // Read-only subscription to the linked experiment, purely so the dataset
  // sync below sees 'dataset_config' factor levels (see its own comment).
  // Same query key the page and FactorBindableField already use, so this
  // shares their cache entry rather than adding a request of its own.
  const experimentFactorsQuery = useQuery({
    queryKey: ['experiments', experimentId],
    queryFn: () => experimentsApi.get(experimentId!),
    enabled: !!experimentId,
  })

  // Keeps the linked experiment's own dataset list (the experiment_datasets
  // join table) in step with the Dataset nodes on this canvas -- see the
  // effect near the autosave below, which is what calls this.
  const syncExperimentDatasets = useMutation({
    mutationFn: (datasetIds: string[]) => experimentsApi.update(experimentId!, { dataset_ids: datasetIds }),
  })

  // Run button's own entry point -- always opens RunConfirmDialog first
  // (whole graph, or the picked cell) rather than firing a real, billable
  // run immediately. That dialog does its own pre-flight scan for obviously
  // misconfigured nodes (no model, no dataset picked, no script code, an
  // agent with nothing wired into its required LLM connector) and surfaces
  // them inline instead of only ever finding out via a generic "one or more
  // nodes failed" AFTER spending a real attempt.
  function requestRun() {
    setRunErrorDismissed(false)
    setPendingRunConfirm(selectedCellLabel ? { type: 'cell', label: selectedCellLabel } : { type: 'graph' })
  }

  function confirmPendingRun() {
    if (!pendingRunConfirm) return
    if (pendingRunConfirm.type === 'node') {
      runNodeMutation.mutate(pendingRunConfirm.nodeId)
    } else {
      runMutation.mutate()
    }
    setPendingRunConfirm(null)
  }

  // A run must always use an immutable published snapshot. When the canvas
  // draft differs, this mutation lets the confirmation dialog make the
  // user's intended choice explicit: publish the draft, then run it.
  const publishAndRunMutation = useMutation({
    mutationFn: () => protocolsApi.publish(protocolId),
    onSuccess: (published) => {
      if (published.experiment_id) {
        queryClient.setQueryData(protocolForExperimentQueryKey(published.experiment_id), published)
      }
      confirmPendingRun()
    },
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

  // Stop button -- only raises cancel_requested_at; run_protocol's own node
  // loop (polled between nodes, not mid-node) is what actually honors it.
  // onSuccess refetches immediately rather than waiting for the next poll
  // tick, so cancel_requested_at (and the "Stopping…" label below) appears
  // right away instead of up to RUN_POLL_MS late.
  const cancelMutation = useMutation({
    mutationFn: () => protocolsApi.cancelRun(protocolId, runId!),
    onSuccess: () => runQuery.refetch(),
  })
  const cancelRequested = !!runQuery.data?.cancel_requested_at

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
  const agentIdsWithLlm = useMemo(() => new Set(edges.filter((e) => e.targetHandle === 'ai').map((e) => e.target)), [edges])

  // The canvas's per-node Play icon is only offered for a node with no
  // upstream *main* pipeline edge (mirrors services.protocol_execution's
  // _upstream_ids: any edge into this node whose targetHandle ISN'T one of
  // the typed connector slots) -- running a node mid-pipeline against real
  // upstream output needs a bounded/partial-run entrypoint this executor
  // doesn't have yet (see NodeHoverToolbar.tsx's own long-standing note on
  // running a single step).
  const agentIdsWithUpstream = useMemo(
    () => new Set(edges.filter((e) => !CONNECTOR_HANDLES.has(e.targetHandle ?? '')).map((e) => e.target)),
    [edges],
  )

  // Which agents have anything the model can actually CALL -- feeds the ReAct
  // pattern node's "this loop won't loop" warning icon below. That node's
  // warning depends on its AGENT's wiring rather than its own config, so it
  // can't be computed inside the node component the way its other warnings
  // are; it's injected here alongside missingLlm instead.
  //
  // Mirrors the backend's three tool sources (services/protocol_execution.py):
  // MCP nodes on the Tool connector (_resolve_tool_config), OKF bundles and
  // documents on Knowledge (_resolve_knowledge_config -- a knowledge source is
  // served by a real MCP server, so at run time it's just more tools), and
  // Skill nodes, which the ReAct loop turns into a bound `load_skill` tool.
  // Script nodes share the Tool connector but are NOT callable --
  // _resolve_script_config folds their code into the prompt text -- so the
  // source's own type is checked, not just the handle it lands on.
  const agentIdsWithCallableTools = useMemo(() => {
    const nodeTypeById = new Map(nodes.map((n) => [n.id, n.type]))
    return new Set(
      edges
        .filter((e) => {
          if (e.targetHandle === 'knowledge' || e.targetHandle === 'skill') return true
          if (e.targetHandle !== 'tool') return false
          const sourceType = nodeTypeById.get(e.source)
          return sourceType === 'mcp_tool' || sourceType === 'mcp_scikit_learn' || sourceType === 'mcp_client_tool'
        })
        .map((e) => e.target),
    )
  }, [nodes, edges])
  const patternHostIds = useMemo(() => {
    const map = new Map<string, string>()
    for (const e of edges) {
      if (e.targetHandle === 'architectural_pattern') map.set(e.source, e.target)
    }
    return map
  }, [edges])

  const nodesWithRunStatus = useMemo((): Node[] => {
    return nodes.map((n) => {
      const patternHostId = patternHostIds.get(n.id)
      return {
        ...n,
        deletable: !nonDeletablePatternNodeIds.has(n.id),
        data: {
          ...n.data,
          runStatus: runQuery.data?.node_runs[n.id]?.status,
          missingLlm: n.type === 'agent' && !agentIdsWithLlm.has(n.id),
          canRunAlone: n.type === 'agent' && !agentIdsWithUpstream.has(n.id),
          // Only meaningful once the pattern is actually wired to an agent --
          // an orphaned pattern node has no loop to warn about.
          hostHasNoTools: !!patternHostId && !agentIdsWithCallableTools.has(patternHostId),
        },
      }
    })
  }, [
    nodes,
    runQuery.data,
    nonDeletablePatternNodeIds,
    agentIdsWithLlm,
    agentIdsWithUpstream,
    agentIdsWithCallableTools,
    patternHostIds,
  ])

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
    setServerBrowserOpen(false)
    setSkillBrowserOpen(false)
    setBundleBrowserOpen(false)
    setDocumentBrowserOpen(false)
    setDatasetBrowserOpen(false)
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
    setServerBrowserOpen(false)
    setAddPanelOpen(true)
  }, [])
  // A MainEdgeAddStub requests this instead -- same panel, restricted to
  // "agent" (the only node type this stub ever creates), and addNode()
  // below wires a plain edge (no handle id) in whichever direction the
  // requesting stub sits.
  const requestMainEdgeAdd = useCallback((request: MainEdgeAddRequest) => {
    setSelectedNodeId(null)
    setPendingMainEdgeAdd(request)
    setServerBrowserOpen(false)
    setAddPanelOpen(true)
  }, [])
  // An InteractEdge's own "+" requests this -- same panel again, restricted
  // to "agent", and addNode() below removes the original edge and rewires
  // origin->newAgent->target instead.
  const requestEdgeInsert = useCallback((request: EdgeInsertRequest) => {
    setSelectedNodeId(null)
    setPendingEdgeInsert(request)
    setServerBrowserOpen(false)
    setAddPanelOpen(true)
  }, [])
  // The canvas's per-node Play icon (NodeHoverToolbar) -- opens
  // RunConfirmDialog scoped to just this node, same as the main Run button,
  // rather than firing runNodeMutation immediately (see its own comment for
  // why that mutation reuses the main Run button's runId/runQuery polling
  // state instead of a separate one).
  const requestRunNode = useCallback(
    (nodeId: string) => {
      setRunErrorDismissed(false)
      const label = (nodes.find((n) => n.id === nodeId)?.data as { label?: string } | undefined)?.label || 'this node'
      setPendingRunConfirm({ type: 'node', nodeId, label })
    },
    [nodes],
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
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'design-impact'] })
      setFactorPickerNodeId(null)
    },
  })

  // Every new Agent gets its own explicit default execution-pattern node
  // (Motoro's own "reason_act" via _resolve_pattern_config) wired in
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
    const patternPosition = findFreePosition(
      [...otherPositions, agentPosition],
      { x: agentPosition.x + connectorNodeOffsetX('agent', 'architectural_pattern'), y: agentPosition.y - 160 },
      CONNECTOR_CHILD_CLEARANCE,
    )
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

  // A "+" on the canvas opens a searchable node-type panel on the right,
  // rather than a static always-visible drag palette.
  // New nodes land near the pane's current center, nudged away from any
  // node already there (findFreePosition) so a fresh node never lands on
  // top of an existing one.
  function addNode(nodeType: string, dataOverride?: ProtocolNode['data']) {
    // Not a node type -- drills into the server browser, keeping whichever
    // pending connector/edge request is in flight so picking a server there
    // still wires the resulting node into the slot that asked for it.
    if (nodeType === MCP_SERVER_BROWSE) {
      setServerBrowserOpen(true)
      return
    }
    // Ditto for skills -- see SKILL_BROWSE in skillCatalog.ts.
    if (nodeType === SKILL_BROWSE) {
      setSkillBrowserOpen(true)
      return
    }
    // Ditto for OKF bundles -- see OKF_BUNDLE_BROWSE in okfCatalog.ts.
    if (nodeType === OKF_BUNDLE_BROWSE) {
      setBundleBrowserOpen(true)
      return
    }
    // Ditto for uploaded OKF documents -- see OKF_DOCUMENT_BROWSE.
    if (nodeType === OKF_DOCUMENT_BROWSE) {
      setDocumentBrowserOpen(true)
      return
    }
    // Ditto for datasets -- see DATASET_BROWSE in datasetCatalog.ts.
    if (nodeType === DATASET_BROWSE) {
      setDatasetBrowserOpen(true)
      return
    }
    if (pendingConnectorAdd) {
      const { nodeId: originId, slot } = pendingConnectorAdd
      const originNode = nodes.find((n) => n.id === originId)
      // Architectural Pattern, Skill, Knowledge and Resource connect from above (their
      // connectors live on the agent's own TOP edge -- see AgentNode.tsx),
      // every other slot from below -- matches agentDefaultPattern's own
      // placement, so a swapped-in replacement pattern node lands in the
      // same spot the auto-created default one did.
      // x is the connector's OWN position along the host's edge, not the
      // host's left corner: with all seven slots dropping their node at the
      // same x, a Tool node could land above the AI connector and every one
      // after the first got shoved onto a ring around that same point, which
      // read as nodes scattered at random rather than as "this one belongs to
      // that connector". findFreePosition then prefers the same row when the
      // spot is taken, so a second Tool sits beside the first.
      const desired = originNode
        ? {
            x: originNode.position.x + connectorNodeOffsetX(originNode.type, slot),
            y: originNode.position.y + (TOP_EDGE_SLOTS.has(slot) ? -160 : 160),
          }
        : screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
      const position = findFreePosition(nodes.map((n) => n.position), desired, CONNECTOR_CHILD_CLEARANCE)
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
      setNodes((nds) =>
        nds
          .filter((n) => n.id !== existingPatternEdge?.source)
          .concat({ id: newId, type: nodeType, position, data: dataOverride ?? defaultDataFor(nodeType) }),
      )
      setEdges((eds) =>
        eds
          .filter((e) => e.id !== existingPatternEdge?.id)
          .concat({ id: newNodeId(), source: newId, sourceHandle: slot, target: originId, targetHandle: slot }),
      )
      setPendingConnectorAdd(null)
      setAddPanelOpen(false)
      // Picking a node from the connector panel goes straight into that
      // node's Inspector to set it up, rather than leaving the user to
      // double-click it themselves.
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
    const newNode: Node = { id: newId, type: nodeType, position, data: dataOverride ?? defaultDataFor(nodeType) }

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

  // Picking a server in the browser -- the node is created with that server
  // already bound (nodeDataForServer) and, when the server has a dedicated
  // node type, as that type. Everything after this point is the ordinary
  // addNode path, so a server node wires into a Tool connector, lands on the
  // canvas, and opens its inspector exactly like any other node.
  function addServerNode(server: McpServer) {
    setServerBrowserOpen(false)
    addNode(presetForServer(server).nodeType, nodeDataForServer(server))
  }

  // Same shape as addServerNode, for a server the user just registered through
  // the browser's pinned "MCP Client Tool" row rather than picked off the list.
  // It gets the dedicated client-tool type regardless of any preset: the point
  // of the node is that this protocol brought its own server.
  function addClientToolNode(server: McpServer) {
    setServerBrowserOpen(false)
    addNode(MCP_CLIENT_TOOL_NODE_TYPE, nodeDataForClientTool(server))
  }

  // Same shape as addServerNode: the node is created with the bundle already
  // bound, and everything after this is the ordinary addNode path.
  function addBundleNode(bundle: OkfBundle) {
    setBundleBrowserOpen(false)
    addNode('okf_bundle', nodeDataForBundle(bundle))
  }

  // Same shape again, for the Knowledge connector's other node type.
  function addDocumentNode(document: OkfDocument) {
    setDocumentBrowserOpen(false)
    addNode('okf_document', nodeDataForDocument(document))
  }

  // Same shape as addServerNode: the node is created with the skill already
  // bound, and everything after this is the ordinary addNode path.
  function addSkillNode(skill: Skill) {
    setSkillBrowserOpen(false)
    addNode('skill', nodeDataForSkill(skill))
  }

  // Same shape again. Attaching the dataset to the linked experiment isn't
  // done here: an experiment can hold several now, and they can leave the
  // canvas as well as join it (delete a Dataset node), so the sync watches
  // the node list instead of hooking this one entry point.
  function addDatasetNode(dataset: Dataset) {
    setDatasetBrowserOpen(false)
    addNode('dataset', nodeDataForDataset(dataset))
  }

  // Debounced autosave: every nodes/edges change schedules a PATCH, reset on
  // the next change -- so a node drag (many onNodesChange firings) or a
  // burst of inspector edits only ever produces one write, 800ms after the
  // user stops. pendingGraphRef tracks the latest not-yet-flushed graph so
  // the unmount effect below can save it immediately -- otherwise this
  // effect's own cleanup (which also runs on unmount, not just on the next
  // nodes/edges change) cancels the timer via clearTimeout with nothing to
  // replace it, silently dropping any edit made within the last 800ms
  // before navigating away (e.g. adding a node then immediately clicking
  // back to the experiment list).
  //
  // Only writes when the persisted shape of the graph actually DIFFERS from
  // what was last loaded/saved (lastSavedGraphRef). Without that guard, this
  // effect fires on mount and again as soon as xyflow measures the freshly
  // mounted nodes, PATCHing back the exact graph it was just handed -- and
  // since a remount re-seeds itself from a react-query cache entry that
  // nothing updated after the previous visit's save, that echo write pushes
  // the STALE graph over the newer one on the server. Server and cache then
  // trade places on every visit, which is what made an added node blink in
  // and out ("gone, back, gone") as you navigated away and back.
  const lastSavedGraphRef = useRef(
    JSON.stringify(toPersistedGraph(initialGraph.nodes as Node[], initialGraph.edges as Edge[])),
  )
  const saveSeqRef = useRef(0)
  const saveGraph = useCallback(
    (graph: ProtocolGraph) => {
      lastSavedGraphRef.current = JSON.stringify(graph)
      const seq = ++saveSeqRef.current
      protocolsApi
        .update(protocolId, { graph })
        .then((updated) => {
          // The other half of the fix above: keep the query entry this
          // canvas is re-seeded from on the next mount in step with what
          // was just persisted, so navigating away and back shows the edit
          // rather than the pre-edit graph. Ignores an out-of-order
          // response so a slow earlier save can't overwrite a later one.
          if (seq !== saveSeqRef.current || !updated.experiment_id) return
          queryClient.setQueryData(protocolForExperimentQueryKey(updated.experiment_id), updated)
        })
        .catch(() => {
          // Best-effort autosave; a transient failure just means the next
          // change's save attempt will carry the current (still-correct)
          // in-memory state forward. Clearing the marker makes sure that
          // next attempt happens even if the graph is edited back to the
          // shape this failed save carried.
          if (seq === saveSeqRef.current) lastSavedGraphRef.current = ''
        })
    },
    [protocolId, queryClient],
  )

  const pendingGraphRef = useRef<ProtocolGraph | null>(null)
  useEffect(() => {
    const graph = toPersistedGraph(nodes, edges)
    if (JSON.stringify(graph) === lastSavedGraphRef.current) return
    pendingGraphRef.current = graph
    const timer = setTimeout(() => {
      pendingGraphRef.current = null
      saveGraph(graph)
    }, AUTOSAVE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [nodes, edges, saveGraph])

  // Keeps the linked experiment's dataset list equal to the Dataset nodes on
  // this canvas -- the experiment record should be able to answer "what data
  // is this experiment about" without anyone parsing the graph. Fire-and-
  // forget, matching FactorBindableField's own immediate-persist convention.
  //
  // Seeded from the graph as LOADED, so this only ever fires on a real user
  // change, never on mount. That matters: the notebook attaches a dataset
  // over the SDK before any canvas exists (spinal_pipeline.ipynb's Step 2),
  // and a mount-time "reconcile" would see zero Dataset nodes and detach it.
  //
  // Every Dataset node counts, wired or not and enabled or not -- putting one
  // on the canvas is the declaration that this experiment is about that data;
  // `enabled` only governs whether the run's prompt mentions it.
  //
  // Factors are in the dependency list alongside nodes because a
  // 'dataset_config' factor's levels are datasets this experiment runs
  // against too (see datasetIdsInGraph) -- and editing those levels from the
  // Design tab's FactorsEditor changes no node at all, so a nodes-only
  // dependency would miss it. This subscribes to the same
  // ['experiments', id] cache entry the page and FactorBindableField already
  // use (TanStack dedupes by key, so it costs no extra request), which is
  // what makes a factor save here land immediately: FactorBindableField
  // invalidates that exact key on success.
  const factors = experimentFactorsQuery.data?.design_spec?.factors ?? EMPTY_FACTORS
  const lastSyncedDatasetIdsRef = useRef(JSON.stringify(datasetIdsInGraph(initialGraph.nodes as Node[])))
  useEffect(() => {
    if (!experimentId) return
    const datasetIds = datasetIdsInGraph(nodes, factors)
    const serialized = JSON.stringify(datasetIds)
    if (serialized === lastSyncedDatasetIdsRef.current) return
    lastSyncedDatasetIdsRef.current = serialized
    syncExperimentDatasets.mutate(datasetIds)
    // syncExperimentDatasets is a stable-enough mutation object; including it
    // would re-run this on every render of the mutation's own state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodes, factors, experimentId])

  // protocolId is stable for this component's whole lifetime (the parent
  // remounts it via `key={protocol.id}` on protocol change -- see
  // ProtocolCanvasPage.tsx), so this only ever fires on a genuine unmount,
  // never mid-life.
  useEffect(() => {
    return () => {
      if (pendingGraphRef.current) saveGraph(pendingGraphRef.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selectedNode = nodes.find((n) => n.id === selectedNodeId) ?? null
  // Computed once per selection change, not per FactorBindableField -- an
  // LLM/Tool/Memory node's plain label alone doesn't say which agent it
  // belongs to (see bindableFields.ts's own comment), so every inspector
  // that wraps a field in "+ Make experimental factor" gets this instead of
  // data.label for that purpose specifically; the header title itself still
  // shows the node's own plain label, unaffected.
  const factorNodeLabel = selectedNode ? agentTracedLabel(selectedNode, edges, nodes) : ''

  // Unbinding a field from the inspector is an explicit decision to stop
  // varying it. When that was the factor's last binding, remove the factor
  // declaration too so the Design panel and the materialized matrix cannot
  // keep advertising a treatment the canvas no longer has.
  async function removeUnboundFactors(nextNodes: Node[], removedNames: string[]) {
    if (!experimentId || removedNames.length === 0) return
    const stillBound = new Set(
      nextNodes.flatMap((node) => Object.values((node.data as { factor_bindings?: Record<string, string> }).factor_bindings ?? {})),
    )
    const orphaned = removedNames.filter((name) => !stillBound.has(name))
    if (orphaned.length === 0) return
    const experiment = await experimentsApi.get(experimentId)
    const factors = experiment.design_spec?.factors ?? []
    const nextFactors = factors.filter((factor) => !orphaned.includes(factor.name))
    if (nextFactors.length === factors.length) return
    await experimentsApi.update(experimentId, { design_spec: { ...experiment.design_spec, factors: nextFactors } })
    queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
    queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'design-impact'] })
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
      | SkillNodeData
      | OkfBundleNodeData
      | OkfDocumentNodeData
      | ScriptNodeData
      | ReasonActPatternNodeData
      | SingleAgentBaselinePatternNodeData,
  ) {
    const previous = nodes.find((node) => node.id === nodeId)
    const oldBindings = (previous?.data as { factor_bindings?: Record<string, string> } | undefined)?.factor_bindings ?? {}
    const nextBindings = (data as { factor_bindings?: Record<string, string> }).factor_bindings ?? {}
    const removedNames = Object.entries(oldBindings)
      .filter(([path, name]) => nextBindings[path] !== name)
      .map(([, name]) => name)
    const nextNodes = nodes.map((node) => (node.id === nodeId ? { ...node, data } : node))
    setNodes(nextNodes)
    void removeUnboundFactors(nextNodes, removedNames)
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
        case 'ai':
          return (
            LLM_NODE_TYPES.includes(sourceNode.type ?? '') &&
            (targetNode.type === 'agent' || targetNode.type === 'critic_gate')
          )
        case 'tool':
          return (
            (MCP_TOOL_NODE_TYPES.includes(sourceNode.type ?? '') || sourceNode.type === 'script') &&
            targetNode.type === 'agent'
          )
        case 'memory':
          return sourceNode.type === 'memory' && targetNode.type === 'agent'
        case 'architectural_pattern':
          return PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') && targetNode.type === 'agent'
        case 'skill':
          return sourceNode.type === 'skill' && targetNode.type === 'agent'
        case 'dataset':
          // The one slot with a cardinality check here, not just a hidden
          // "+" stub (see AgentNode.tsx's Dataset comment, and ai/memory
          // above, which are capped the same way but rely on the stub
          // alone). A second dataset on one agent isn't merely unsupported
          // -- every cell resolves ONE workspace, so seed_cell_workspace
          // rejects it at run time, after the run has already started.
          // Comparing datasets is a 'dataset_config' factor instead.
          return (
            sourceNode.type === 'dataset' &&
            targetNode.type === 'agent' &&
            !edges.some(
              (e) => e.target === connection.target && e.targetHandle === 'dataset' && e.source !== connection.source,
            )
          )
        case 'knowledge':
          // The one connector with two source types -- bundles and uploaded
          // documents are interchangeable here, since both resolve to the same
          // per-directory OKF server.
          return KNOWLEDGE_NODE_TYPES.includes(sourceNode.type ?? '') && targetNode.type === 'agent'
        default:
          // A plain "main" pipeline edge -- LLM/memory/pattern/mcp_tool/
          // dataset/skill/knowledge/script nodes have no main handle to drag from in
          // the first place, so this mostly guards against a stray
          // connection, not real interactive use.
          return (
            !LLM_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'memory' &&
            !MCP_TOOL_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'dataset' &&
            sourceNode.type !== 'skill' &&
            !KNOWLEDGE_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'script' &&
            !PATTERN_NODE_TYPES.includes(sourceNode.type ?? '')
          )
      }
    },
    [nodes, edges],
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
  // replace, per the user's own choice for import behavior): every
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
            // zoomed instead of panning. Flipping this pair (matching
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
          <CanvasControls onTidy={tidyUp} />
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
              // A full-text, wrapping, dismissible banner for a run failure
              // (the complete message plus a close button) rather than this
              // app's usual single-line truncate+title-tooltip idiom, which
              // hides exactly the detail (e.g. "No anthropic credential
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
            {cellOptions.length > 0 && (
              <span title={designRegenerationRequired ? 'Design changed — review and regenerate before selecting a cell.' : undefined}>
                <Button
                  size="sm"
                  variant="outline"
                  className="max-w-40"
                  disabled={designRegenerationRequired}
                  onClick={() => setCellPickerOpen(true)}
                >
                  <span className={`truncate ${selectedCellLabel ? 'font-mono' : ''}`} title={selectedCellLabel ?? undefined}>
                    {designRegenerationRequired ? 'Review & regenerate' : selectedCellLabel ?? 'Run cell'}
                  </span>
                </Button>
              </span>
            )}
            {cellPickerOpen && (
              <SelectCellDialog
                cells={cellOptions}
                designSpec={cellPickerExperimentQuery.data?.design_spec}
                selectedCellLabel={selectedCellLabel}
                onCancel={() => setCellPickerOpen(false)}
                onSelect={(cellLabel) => {
                  setSelectedCellLabel(cellLabel)
                  setCellPickerOpen(false)
                }}
              />
            )}
            <Button size="sm" disabled={isRunning} onClick={requestRun}>
              <Play className="size-4" />
              {isRunning ? 'Running…' : selectedCellLabel ? `Run cell: ${selectedCellLabel}` : 'Run'}
            </Button>
            {isRunning && (
              <Button
                size="sm"
                variant="outline"
                disabled={cancelRequested || cancelMutation.isPending}
                onClick={() => cancelMutation.mutate()}
              >
                <Square className="size-4" />
                {cancelRequested ? 'Stopping…' : 'Stop'}
              </Button>
            )}
            <Button
              size="icon"
              className="rounded-full"
              aria-label="Add node"
              onClick={() => {
                setSelectedNodeId(null)
                setPendingConnectorAdd(null)
                setServerBrowserOpen(false)
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
        {addPanelOpen && serverBrowserOpen ? (
          <McpServerBrowserPanel
            onPick={addServerNode}
            onConnect={addClientToolNode}
            onBack={() => setServerBrowserOpen(false)}
            onClose={closeAddPanel}
            revealHiddenServers={revealsHiddenMcpServers(nodes)}
          />
        ) : addPanelOpen && skillBrowserOpen ? (
          <SkillBrowserPanel onPick={addSkillNode} onBack={() => setSkillBrowserOpen(false)} onClose={closeAddPanel} />
        ) : addPanelOpen && bundleBrowserOpen ? (
          <OkfBundleBrowserPanel
            onPick={addBundleNode}
            onBack={() => setBundleBrowserOpen(false)}
            onClose={closeAddPanel}
          />
        ) : addPanelOpen && documentBrowserOpen ? (
          <OkfDocumentBrowserPanel
            onPick={addDocumentNode}
            onBack={() => setDocumentBrowserOpen(false)}
            onClose={closeAddPanel}
          />
        ) : addPanelOpen && datasetBrowserOpen ? (
          <DatasetBrowserPanel
            onPick={addDatasetNode}
            onBack={() => setDatasetBrowserOpen(false)}
            onClose={closeAddPanel}
          />
        ) : addPanelOpen ? (
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
        ) : MCP_TOOL_NODE_TYPES.includes(selectedNode?.type ?? '') ? (
          <McpToolNodeInspector
            node={{ id: selectedNode!.id, type: selectedNode!.type!, position: selectedNode!.position, data: selectedNode!.data as McpToolNodeData }}
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
            nodeRun={runQuery.data?.node_runs[selectedNode.id]}
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
        ) : selectedNode?.type === 'skill' ? (
          <SkillNodeInspector
            node={{ id: selectedNode.id, type: 'skill', position: selectedNode.position, data: selectedNode.data as SkillNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'okf_bundle' ? (
          <OkfBundleNodeInspector
            node={{ id: selectedNode.id, type: 'okf_bundle', position: selectedNode.position, data: selectedNode.data as OkfBundleNodeData }}
            experimentId={experimentId}
            factorNodeLabel={factorNodeLabel}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'okf_document' ? (
          <OkfDocumentNodeInspector
            node={{ id: selectedNode.id, type: 'okf_document', position: selectedNode.position, data: selectedNode.data as OkfDocumentNodeData }}
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
      {pendingRunConfirm && (
        <RunConfirmDialog
          scope={pendingRunConfirm}
          nodes={nodes}
          edges={edges}
          queryClient={queryClient}
          onCancel={() => setPendingRunConfirm(null)}
          onConfirm={confirmPendingRun}
          hasUnpublishedChanges={hasUnpublishedChanges}
          publishedRevision={publishedRevision}
          isPublishing={publishAndRunMutation.isPending}
          publishError={
            publishAndRunMutation.error instanceof ApiError && typeof publishAndRunMutation.error.detail === 'string'
              ? publishAndRunMutation.error.detail
              : publishAndRunMutation.isError
                ? 'Could not publish the latest canvas.'
                : null
          }
          onPublishAndRun={() => publishAndRunMutation.mutate()}
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
          revealHiddenServers={revealsHiddenMcpServers(nodes)}
          onSave={(factor, field) => {
            if (field) createFactorMutation.mutate({ factor, field })
          }}
        />
      )}
    </ProtocolCanvasActionsProvider>
  )
})
