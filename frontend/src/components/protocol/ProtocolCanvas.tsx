import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  addEdge,
  Background,
  MarkerType,
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
import { Lock, Play, Plus, Square, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ApiError, experimentsApi, protocolsApi } from '@/api/client'
import { CONNECTOR_HANDLES } from '@/lib/coordinationStrategy'
import { newNodeId } from '@/lib/nodeId'
import { handoffPeers, promptReferenceScope } from '@/lib/promptReferences'
import { mergeProtocolSaveIntoCache, protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import { TERMINAL_RUN_STATUSES } from '@/lib/protocolRun'
import { nodeDisplayNames } from '@/lib/nodeNames'
import { raiseForTruncation, suggestedMaxIterations } from '@/lib/reasonActIterations'
import {
  defaultAgentNodeData,
  defaultAnthropicModelNodeData,
  defaultAzureFoundryModelNodeData,
  defaultCriticGateNodeData,
  defaultDatasetNodeData,
  defaultLocalModelNodeData,
  defaultMcpToolNodeData,
  defaultMemoryNodeData,
  defaultOpenAiModelNodeData,
  defaultOpenRouterModelNodeData,
  defaultOutputParserNodeData,
  defaultReasonActPatternNodeData,
  defaultScriptNodeData,
  defaultSingleAgentBaselinePatternNodeData,
} from '@/types/protocols'
import type {
  AgentNodeData,
  CriticGateNodeData,
  DatasetNodeData,
  ModelNodeData,
  McpToolNodeData,
  MemoryNodeData,
  NodeRunState,
  OkfBundleNodeData,
  OkfDocumentNodeData,
  OutputParserNodeData,
  Protocol,
  ProtocolEdge,
  ProtocolGraph,
  ProtocolNode,
  ReasonActPatternNodeData,
  ScriptNodeData,
  SingleAgentBaselinePatternNodeData,
  SkillNodeData,
  TestRun,
} from '@/types/protocols'
import type { Dataset } from '@/types/datasets'
import type { DesignFactor } from '@/types/experiments'
import type { McpServer } from '@/types/mcpServers'
import type { OkfBundle, OkfDocument } from '@/types/okf'
import type { Skill } from '@/types/skills'
import { AddNodePanel } from './AddNodePanel'
import { AgentNodeInspector } from './AgentNodeInspector'
import { agentTracedLabel, factorBoundField, revealsHiddenMcpServers, toolFactorServerId, unboundBindableFields, type UnboundField } from './bindableFields'
import { CanvasControls } from './CanvasControls'
import { CriticGateNodeInspector } from './CriticGateNodeInspector'
import { DatasetNodeInspector } from './DatasetNodeInspector'
import { DeleteNodeConfirmDialog } from './DeleteNodeConfirmDialog'
import { DEFAULT_ZOOM } from './constants'
import { FactorEditorDialog } from './FactorEditorDialog'
import {
  CONNECTOR_CHILD_CLEARANCE,
  SUB_AGENT_CHILD_OFFSET_Y,
  connectorNodeOffsetX,
  findFreePosition,
  tidyLayout,
} from './layout'
import { ModelNodeInspector } from './ModelNodeInspector'
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
import { OutputParserNodeInspector } from './OutputParserNodeInspector'
import {
  ProtocolCanvasActionsProvider,
  type ConnectorAddRequest,
  type ConnectorSlot,
  type EdgeInsertRequest,
  type MainEdgeAddRequest,
} from './ProtocolCanvasContext'
import { ReasonActPatternNodeInspector } from './ReasonActPatternNodeInspector'
import { RunConfirmDialog } from './RunConfirmDialog'
import { ReopenTestRunResultsButton, TestRunResults } from './TestRunResults'
import type { RunScope } from './runSummary'
import { ScriptNodeInspector } from './ScriptNodeInspector'
import { SingleAgentBaselinePatternNodeInspector } from './SingleAgentBaselinePatternNodeInspector'
import { OkfBundleBrowserPanel } from './OkfBundleBrowserPanel'
import { OKF_BUNDLE_BROWSE, OKF_DOCUMENT_BROWSE, nodeDataForBundle, nodeDataForDocument } from './okfCatalog'
import { OkfBundleNodeInspector } from './OkfBundleNodeInspector'
import { OkfDocumentBrowserPanel } from './OkfDocumentBrowserPanel'
import { OkfDocumentNodeInspector } from './OkfDocumentNodeInspector'
import { SkillBrowserPanel } from './SkillBrowserPanel'
import { SKILL_BROWSE, nodeDataForSkill } from './skillCatalog'
import { SkillNodeInspector } from './SkillNodeInspector'
import { ConversationTranscript } from './ConversationTranscript'
import { InteractEdge } from './edges/InteractEdge'
import { AgentNode } from './nodes/AgentNode'
import { CriticGateNode } from './nodes/CriticGateNode'
import { DatasetNode } from './nodes/DatasetNode'
import { ModelNode } from './nodes/ModelNode'
import { McpClientToolNode } from './nodes/McpClientToolNode'
import { McpToolNode } from './nodes/McpToolNode'
import { MemoryNode } from './nodes/MemoryNode'
import { OutputParserNode } from './nodes/OutputParserNode'
import { ReasonActPatternNode } from './nodes/ReasonActPatternNode'
import { ScriptNode } from './nodes/ScriptNode'
import { SingleAgentBaselinePatternNode } from './nodes/SingleAgentBaselinePatternNode'
import { OkfBundleNode } from './nodes/OkfBundleNode'
import { OkfDocumentNode } from './nodes/OkfDocumentNode'
import { SkillNode } from './nodes/SkillNode'
import { ProtocolCanvasMenu } from './ProtocolCanvasMenu'

// One node type per LLM provider / architectural pattern (see ModelNodeData/
// ReasonActPatternNodeData's own comments in types/protocols.ts) -- each
// connector slot accepts this whole family, not one exact type, mirroring
// how the "tool" slot already accepts any mcp_tool node.
const MODEL_NODE_TYPES = ['model_anthropic', 'model_openai', 'model_azure_foundry', 'model_openrouter', 'model_local']
const PATTERN_NODE_TYPES = ['pattern_reason_act', 'pattern_single_agent_baseline']
// The Knowledge slot's family, mirroring _KNOWLEDGE_NODE_TYPES in
// services/protocol_execution.py: a server-side folder or an uploaded single
// concept, both resolved identically into the agent's tool allow-list.
const KNOWLEDGE_NODE_TYPES = ['okf_bundle', 'okf_document']
// The four connector slots that live on an Agent's TOP edge (see
// AgentNode.tsx) -- a node feeding one of these is placed ABOVE its agent,
// every other slot's source below it.
const TOP_EDGE_SLOTS = new Set<ConnectorSlot>(['architectural_pattern', 'skill', 'dataset', 'knowledge'])

const NODE_TYPES = {
  agent: AgentNode,
  sub_agent: AgentNode,
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
  model_anthropic: ModelNode,
  model_openai: ModelNode,
  model_azure_foundry: ModelNode,
  model_openrouter: ModelNode,
  model_local: ModelNode,
  memory: MemoryNode,
  output_parser: OutputParserNode,
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
  if (nodeType === 'model_anthropic') return defaultAnthropicModelNodeData()
  if (nodeType === 'model_openai') return defaultOpenAiModelNodeData()
  if (nodeType === 'model_azure_foundry') return defaultAzureFoundryModelNodeData()
  if (nodeType === 'model_openrouter') return defaultOpenRouterModelNodeData()
  if (nodeType === 'model_local') return defaultLocalModelNodeData()
  if (nodeType === 'memory') return defaultMemoryNodeData()
  if (nodeType === 'output_parser') return defaultOutputParserNodeData()
  if (nodeType === 'dataset') return defaultDatasetNodeData()
  if (nodeType === 'script') return defaultScriptNodeData()
  if (nodeType === 'pattern_reason_act') return defaultReasonActPatternNodeData()
  if (nodeType === 'pattern_single_agent_baseline') return defaultSingleAgentBaselinePatternNodeData()
  return defaultAgentNodeData(nodeType === 'sub_agent' ? 'Sub-Agent' : 'Agent')
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
// family of node types (MODEL_NODE_TYPES/PATTERN_NODE_TYPES above) rather than
// the full catalog. Tool's own family includes Script alongside mcp_tool
// (one connector accepting several kinds of node -- see AgentNode.tsx's own
// comment on its Tool handle): a Script is a pure config source with no
// callable capability of its own, so it shares Tool's slot rather than
// getting a dedicated one. Dataset used to share it too, but now has its
// own slot -- what an agent operates ON, not a capability it operates WITH.
const CONNECTOR_PANEL_INFO: Record<ConnectorSlot, { allowedTypes: string[]; title: string }> = {
  model: { allowedTypes: MODEL_NODE_TYPES, title: 'Add Model' },
  tool: { allowedTypes: [MCP_SERVER_BROWSE, 'script'], title: 'Add Tool' },
  memory: { allowedTypes: ['memory'], title: 'Add Memory' },
  output_parser: { allowedTypes: ['output_parser'], title: 'Add Output Parser' },
  architectural_pattern: { allowedTypes: PATTERN_NODE_TYPES, title: 'Add Architectural Pattern' },
  skill: { allowedTypes: [SKILL_BROWSE], title: 'Add Skill' },
  dataset: { allowedTypes: [DATASET_BROWSE], title: 'Add Dataset' },
  // The one slot with two entries in its panel: knowledge arrives either as a
  // folder already on the server (bundle) or as a file the user uploads
  // (document), and which of those you have is the question the panel asks.
  knowledge: { allowedTypes: [OKF_BUNDLE_BROWSE, OKF_DOCUMENT_BROWSE], title: 'Add Knowledge' },
  sub_agents: { allowedTypes: ['sub_agent'], title: 'Add Sub-Agent' },
}

// Where an Output Parser belongs relative to the agent it serves: under that
// agent's own Parser connector, nudged clear of anything already sitting there.
// Shared by the two paths that create a parser without going through the
// connector's "+" -- converting a legacy stored contract, and adding one from
// the unrestricted toolbar panel -- so a parser lands in the same place however
// it came to exist.
function parserPositionFor(agent: Node, otherNodes: Node[]) {
  return findFreePosition(
    otherNodes.map((n) => n.position),
    { x: agent.position.x + connectorNodeOffsetX(agent.type, 'output_parser'), y: agent.position.y + 160 },
    CONNECTOR_CHILD_CLEARANCE,
  )
}

// Connector slots have been renamed since graphs started being saved, and a
// slot id lives in persisted data (it's the edge's source/targetHandle):
//
//   * "llm" -> "ai" -> "model", on every edge as the connector vocabulary evolved.
//   * "llm_*" -> "model_*", for the five provider-node discriminators.
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
// resolving the old spellings (_LEGACY_MODEL_HANDLES / _LEGACY_DATASET_HANDLES in
// services/protocol_execution.py) so a graph that's never opened still runs,
// and this covers a tab that loaded before the deploy and is still autosaving
// old-spelling edges.
const LEGACY_MODEL_NODE_TYPES: Record<string, string> = {
  llm_anthropic: 'model_anthropic',
  llm_openai: 'model_openai',
  llm_azure_foundry: 'model_azure_foundry',
  llm_openrouter: 'model_openrouter',
  llm_local: 'model_local',
}

function migrateLegacyNodes(graph: ProtocolGraph): Node[] {
  return (graph.nodes as Node[]).map((node) => {
    const type = LEGACY_MODEL_NODE_TYPES[node.type ?? '']
    return type ? { ...node, type } : node
  })
}

function migrateLegacyHandles(graph: ProtocolGraph): Edge[] {
  const datasetIds = new Set(graph.nodes.filter((n) => n.type === 'dataset').map((n) => n.id))
  return (graph.edges as Edge[]).map((e) => {
    if (e.targetHandle === 'ai' || e.targetHandle === 'llm' || e.sourceHandle === 'ai' || e.sourceHandle === 'llm') {
      return {
        ...e,
        sourceHandle: e.sourceHandle === 'ai' || e.sourceHandle === 'llm' ? 'model' : e.sourceHandle,
        targetHandle: e.targetHandle === 'ai' || e.targetHandle === 'llm' ? 'model' : e.targetHandle,
      }
    }
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
  experimentLocked?: boolean
}>(function ProtocolCanvas({ protocolId, experimentId, initialGraph, hasUnpublishedChanges, publishedRevision, experimentLocked = false }, canvasHandleRef) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(migrateLegacyNodes(initialGraph))
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
    () => ({
      bindFactor: bindFactorOnNode,
      removeFactorBindings,
      renameFactorBindings,
    }),
    [bindFactorOnNode, removeFactorBindings, renameFactorBindings],
  )
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  useEffect(() => {
    if (!experimentLocked) return
    // A lock can be applied from the canvas menu while an inspector is open.
    // Close that editor immediately so it never looks as if its fields can
    // still be changed; the node cards remain visible on the canvas.
    setSelectedNodeId(null)
    setAddPanelOpen(false)
  }, [experimentLocked])
  // The node whose hover-toolbar "Make experimental factor" icon was just
  // clicked -- opens FactorEditorDialog's field picker pre-filtered to just
  // that node's own unbound fields (see requestMakeFactor below).
  const [factorPickerNodeId, setFactorPickerNodeId] = useState<string | null>(null)
  const [editingFactorName, setEditingFactorName] = useState<string | null>(null)
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
  // Populated by each node's per-node Play icon (requestRunNode below) -- opens
  // RunConfirmDialog instead of firing the run immediately, so a real
  // (billable) run never fires without the user seeing what will actually
  // execute first.
  const [pendingRunConfirm, setPendingRunConfirm] = useState<RunScope | null>(null)
  // Conversation mode's own confirm state. Separate from pendingRunConfirm
  // because the dialog needs two fields filled in (who to ask, and what) before
  // the scope it confirms even exists.
  const [runId, setRunId] = useState<string | null>(null)
  const [testResultsOpen, setTestResultsOpen] = useState(false)
  const [playResultsOpen, setPlayResultsOpen] = useState(false)
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

  const onConnect = useCallback((connection: Connection) => {
    if (!experimentLocked) setEdges((eds) => addEdge(connection, eds))
  }, [experimentLocked, setEdges])

  // "Tidy up" -- reposition every node into a generated layout (see
  // layout.ts's tidyLayout). Goes through this component's own setNodes
  // rather than useReactFlow().setNodes because the flow is controlled, and
  // that's also what lets the existing debounced autosave pick the new
  // positions up with no extra request wiring. fitView waits a frame so it
  // measures the moved nodes, not the ones they replaced.
  const tidyUp = useCallback(() => {
    if (experimentLocked) return
    setNodes((nds) => {
      const positions = tidyLayout(nds, edges)
      return nds.map((n) => {
        const position = positions.get(n.id)
        return position ? { ...n, position } : n
      })
    })
    requestAnimationFrame(() => fitView({ maxZoom: DEFAULT_ZOOM, duration: 300 }))
  }, [edges, experimentLocked, fitView, setNodes])

  // Read-only subscription to the linked experiment, for two things the canvas
  // can't read off the graph: the 'dataset_config' factor levels the dataset
  // sync below needs (see its own comment), and the coordination strategy,
  // which decides whether an agent's "Lead" marker means anything yet.
  // Same query key the page and FactorBindableField already use, so this
  // shares their cache entry rather than adding a request of its own.
  const experimentQuery = useQuery({
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

  function confirmPendingRun() {
    if (pendingRunConfirm?.type === 'node') {
      runNodeMutation.mutate(pendingRunConfirm.nodeId)
      setPendingRunConfirm(null)
      return
    }
    if (pendingRunConfirm?.type === 'graph') {
      testRunMutation.mutate()
      setPendingRunConfirm(null)
      return
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

  // The whole canvas, once, with no factor values substituted in. Deliberately
  // NOT a second way to run a cell: picking a replicate, and running the
  // pending batch, both live in the Runs tab, and duplicating either here
  // would give the same action two homes that can disagree. This button is
  // the answer to "there are no cells, how do I run this at all" -- an
  // experiment with a design still runs from the Runs tab.
  const testRunMutation = useMutation({
    mutationFn: () => protocolsApi.testRun(protocolId),
    onSuccess: (run) => {
      setRunId(run.id)
      setPlayResultsOpen(false)
      setTestResultsOpen(true)
      queryClient.setQueryData(['protocols', protocolId, 'test-run'], run)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      queryClient.invalidateQueries({ queryKey: ['protocols', protocolId, 'test-run'] })
    },
  })

  // The canvas's per-node Play icon stores its run in the shared runId/runQuery
  // polling state, so the
  // Output tab and status badge both already read from that same state
  // with no changes needed.
  const runNodeMutation = useMutation({
    mutationFn: (nodeId: string) => protocolsApi.runNode(protocolId, nodeId),
    onSuccess: (run) => {
      setRunId(run.id)
      setTestResultsOpen(false)
      setPlayResultsOpen(true)
    },
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

  const testRunQuery = useQuery({
    queryKey: ['protocols', protocolId, 'test-run'],
    queryFn: () => protocolsApi.getLatestTestRun(protocolId),
    enabled: !!experimentId,
    refetchInterval: (query) => {
      const status = query.state.data?.status
      if (testResultsOpen) return RUN_POLL_MS
      return status && TERMINAL_RUN_STATUSES.has(status) ? false : RUN_POLL_MS
    },
  })

  const playResult: TestRun | null = runQuery.data?.target_node_id ? {
    id: runQuery.data.id,
    protocol_id: runQuery.data.protocol_id,
    status: runQuery.data.status,
    error: runQuery.data.error,
    protocol_revision_id: runQuery.data.protocol_revision_id,
    created_at: runQuery.data.created_at,
    updated_at: runQuery.data.updated_at,
    observations: runQuery.data.observations,
    artifacts: runQuery.data.artifacts,
    conversation: runQuery.data.conversation,
    tested_published_revision: null,
    freshness: { out_of_date: false, reasons: [] },
    resources: {
      task: { duration_seconds: null, cost_usd: null },
      evaluation: { duration_seconds: null, cost_usd: null },
      total: { duration_seconds: null, cost_usd: null },
    },
    execution_summary: {
      node_runs: runQuery.data.node_runs,
      started_at: null,
      completed_at: null,
      cancel_requested_at: runQuery.data.cancel_requested_at,
    },
  } : null

  // What this protocol last did. `runId` above is React state set only by the
  // mutation that launches a run, so before this the canvas could only ever
  // show a run started in this very browser tab: a reload dropped the run it
  // was watching, and a run started outside the GUI (the SDK, a notebook, a
  // direct API call) could never be watched at all -- its node statuses,
  // outputs and conversation transcript existed but had no way to be reached.
  // list_protocol_runs is newest-first, so [0] is the latest.
  const protocolRunsQuery = useQuery({
    queryKey: ['protocols', protocolId, 'runs'],
    queryFn: () => protocolsApi.listRuns(protocolId),
  })

  // Seeded once and only into an empty slot: a run launched here must win over
  // whatever happened to be newest when the page loaded, and re-seeding on
  // every refetch would yank the view off the run the user is watching the
  // moment someone else's run lands.
  const seededLatestRun = useRef(false)
  useEffect(() => {
    if (seededLatestRun.current || runId) return
    const latest = protocolRunsQuery.data?.[0]
    if (!latest) return
    seededLatestRun.current = true
    setRunId(latest.id)
  }, [protocolRunsQuery.data, runId])

  const isRunning =
    testRunMutation.isPending ||
    runNodeMutation.isPending ||
    (!!testRunQuery.data && !TERMINAL_RUN_STATUSES.has(testRunQuery.data.status)) ||
    (!!runQuery.data && !TERMINAL_RUN_STATUSES.has(runQuery.data.status))

  // Stop button -- only raises cancel_requested_at; run_protocol's own node
  // loop (polled between nodes, not mid-node) is what actually honors it.
  // onSuccess refetches immediately rather than waiting for the next poll
  // tick, so cancel_requested_at (and the "Stopping…" label below) appears
  // right away instead of up to RUN_POLL_MS late.
  const cancelMutation = useMutation({
    mutationFn: () => protocolsApi.cancelRun(protocolId, testRunQuery.data?.id ?? runId!),
    onSuccess: () => {
      runQuery.refetch()
      testRunQuery.refetch()
    },
  })
  const cancelRequested = !!testRunQuery.data?.execution_summary.cancel_requested_at || !!runQuery.data?.cancel_requested_at
  const showStandaloneConversation = !!runQuery.data?.conversation && runQuery.data.id !== testRunQuery.data?.id

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
  const agentIdsWithModel = useMemo(() => new Set(edges.filter((e) => e.targetHandle === 'model').map((e) => e.target)), [edges])
  const agentIdsWithParser = useMemo(
    () => new Set(edges.filter((e) => e.targetHandle === 'output_parser').map((e) => e.target)),
    [edges],
  )
  const agentIdsWithSubAgents = useMemo(
    () => {
      const activeSubAgents = new Set(
        nodes.filter((node) => node.type === 'sub_agent' && node.data.active !== false).map((node) => node.id),
      )
      return new Set(
        edges.filter((edge) => edge.targetHandle === 'sub_agents' && activeSubAgents.has(edge.source)).map((edge) => edge.target),
      )
    },
    [edges, nodes],
  )
  const connectedActiveSubAgentIds = useMemo(() => {
    const activeSubAgents = new Set(
      nodes.filter((node) => node.type === 'sub_agent' && node.data.active !== false).map((node) => node.id),
    )
    return new Set(
      edges.filter((edge) => edge.targetHandle === 'sub_agents' && activeSubAgents.has(edge.source)).map((edge) => edge.source),
    )
  }, [edges, nodes])

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
  // are; it's injected here alongside missingModel instead.
  //
  // Mirrors the backend's three tool sources (services/protocol_execution.py):
  // MCP nodes on the Tool connector (_resolve_tool_config), OKF bundles and
  // documents on Knowledge (_resolve_knowledge_config -- a knowledge source is
  // served by a real MCP server, so at run time it's just more tools), and
  // Skill nodes, which the ReAct loop turns into a bound `load_skill` tool.
  // Script nodes share the Tool connector and implicitly grant the script
  // runner, so they count as callable just like explicit MCP Tool nodes.
  const agentIdsWithCallableTools = useMemo(() => {
    const nodeTypeById = new Map(nodes.map((n) => [n.id, n.type]))
    const activeSubAgents = new Set(
      nodes.filter((node) => node.type === 'sub_agent' && node.data.active !== false).map((node) => node.id),
    )
    return new Set(
      edges
        .filter((e) => {
          if (e.targetHandle === 'sub_agents') return activeSubAgents.has(e.source)
          if (e.targetHandle === 'knowledge' || e.targetHandle === 'skill') return true
          if (e.targetHandle !== 'tool') return false
          const sourceType = nodeTypeById.get(e.source)
          return sourceType === 'mcp_tool' || sourceType === 'mcp_scikit_learn' || sourceType === 'mcp_client_tool' || sourceType === 'script'
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

  // The iteration cap each Reason+Act node's driven agent actually needs
  // (lib/reasonActIterations.ts), by pattern node id. Computed here, once for
  // the whole canvas, because it depends on the AGENT's wiring rather than the
  // pattern node's own data -- and because the node card's warning triangle
  // and the inspector's "Use N" hint have to agree on the number.
  const wiringIterationsByPattern = useMemo(() => {
    const patternIds = nodes.filter((n) => n.type === 'pattern_reason_act').map((n) => n.id)
    if (patternIds.length === 0) return new Map<string, number | null>()
    const graph = toPersistedGraph(nodes, edges)
    return new Map(patternIds.map((id) => [id, suggestedMaxIterations(graph, id)]))
  }, [nodes, edges])

  // The most recent thing this canvas actually did, whichever kind it was.
  // Node badges, inspectors' Input/Output and config findings all read this,
  // not `runQuery` alone: list_protocol_runs excludes test runs, so after a
  // page reload runQuery can never be seeded with one, and a Test Run's node
  // output vanished from the inspectors while its results panel still showed
  // it. Newest wins, so running for real supersedes an earlier Test Run.
  const latestNodeRuns = useMemo(() => {
    const run = runQuery.data
    const test = testRunQuery.data
    if (!run) return test?.execution_summary.node_runs
    if (!test) return run.node_runs
    return test.created_at > run.created_at ? test.execution_summary.node_runs : run.node_runs
  }, [runQuery.data, testRunQuery.data])

  // What that run's own loop reported, by PATTERN node id: a Reason+Act agent
  // that exhausted its ceiling (services/protocol_execution.py's
  // _truncation_fields) leaves the marker on the AGENT's node_run, but the cap
  // that caused it is configured on the pattern node driving that agent, so the
  // finding has to be carried across the edge to be actionable.
  const truncationByPattern = useMemo(() => {
    const map = new Map<string, NodeRunState['truncation']>()
    for (const n of nodes) {
      if (n.type !== 'pattern_reason_act') continue
      const hostId = patternHostIds.get(n.id)
      const truncation = hostId ? latestNodeRuns?.[hostId]?.truncation : null
      if (truncation) map.set(n.id, truncation)
    }
    return map
  }, [nodes, patternHostIds, latestNodeRuns])

  // Just the caps, for the pre-run scan (findNodeConfigIssues), which names
  // the node but has no run of its own to read.
  const truncatedCaps = useMemo(() => {
    const map = new Map<string, number>()
    for (const [patternId, truncation] of truncationByPattern) {
      if (typeof truncation?.max_iterations === 'number') map.set(patternId, truncation.max_iterations)
    }
    return map
  }, [truncationByPattern])

  // A truncated run outranks the wiring estimate -- see raiseForTruncation.
  const suggestedIterationsByPattern = useMemo(
    () =>
      new Map(
        [...wiringIterationsByPattern].map(([id, wiring]) => [
          id,
          raiseForTruncation(wiring, truncationByPattern.get(id)?.max_iterations),
        ]),
      ),
    [wiringIterationsByPattern, truncationByPattern],
  )

  // Who each agent may consult under the Peer Collaboration coordination
  // strategy, mirroring services/protocol_execution.py's _connected_agent_ids:
  // a plain (non-connector) edge joining two Agent nodes, read undirected. The
  // same edge is still a directed pipeline edge for a sequential run -- it is
  // both, and the experiment's strategy decides which. Nothing on the canvas is
  // drawn differently for it; this only feeds AgentNode's tool-calling warning,
  // which is about the agent, not the edge.
  const peerIdsByAgent = useMemo(() => {
    const nodeTypeById = new Map(nodes.map((n) => [n.id, n.type]))
    const map = new Map<string, string[]>()
    const link = (a: string, b: string) => map.set(a, [...(map.get(a) ?? []), b])
    for (const e of edges) {
      if (CONNECTOR_HANDLES.has(e.targetHandle ?? '')) continue
      if (nodeTypeById.get(e.source) !== 'agent' || nodeTypeById.get(e.target) !== 'agent') continue
      if (e.source === e.target) continue
      link(e.source, e.target)
      link(e.target, e.source)
    }
    return map
  }, [nodes, edges])

  // The agent currently marked as the conversation lead, if any. Read off the
  // graph rather than tracked in state so it survives a canvas reload, and
  // computed here rather than in the inspector because the inspector only ever
  // sees the one node it's editing. Feeds the inspector's rule that the
  // checkbox is offered on the marked agent and on nobody else once a lead
  // exists -- the marker is single-valued (two is a validation error
  // server-side), so the UI shouldn't let you create the second one.
  const markedLeadAgentId = useMemo(
    () => nodes.find((n) => n.type === 'agent' && (n.data as AgentNodeData).conversation_lead === true)?.id ?? null,
    [nodes],
  )

  // Same reasoning as markedLeadAgentId: the inspector edits one node and can't
  // see the wiring around it, so which upstream outputs that node's prompt may
  // reference is resolved here. Recomputed on every rewire, which is the point
  // -- the picker has to answer "what's available to me" while the canvas is
  // being drawn, not at publish time.
  const referenceScope = useMemo(
    () => promptReferenceScope(nodes as unknown as ProtocolNode[], edges as unknown as ProtocolEdge[], selectedNodeId),
    [nodes, edges, selectedNodeId],
  )
  // The same answer for an arbitrary node, which the factor-level editor needs
  // -- the field a factor binds to is picked inside that dialog, so the node
  // isn't known until then.
  const promptScopeFor = useCallback(
    (nodeId: string) =>
      promptReferenceScope(nodes as unknown as ProtocolNode[], edges as unknown as ProtocolEdge[], nodeId),
    [nodes, edges],
  )
  // Who is wired to the selected node, either way -- the inspector's
  // Receives/Sends readout. Direct neighbours, unlike referenceScope's
  // transitive ancestry: this one answers "what is wired to me", which is what
  // a user checks against the canvas in front of them.
  const selectedHandoffPeers = useMemo(
    () => handoffPeers(nodes as unknown as ProtocolNode[], edges as unknown as ProtocolEdge[], selectedNodeId),
    [nodes, edges, selectedNodeId],
  )
  // The Output Parser wired into the selected agent, if any -- its label, or
  // null for "none". Same reasoning as markedLeadAgentId again: the inspector
  // sees one node, and whether a parser hangs off it is wiring.
  const selectedOutputParserLabel = useMemo(() => {
    if (!selectedNodeId) return null
    const edge = edges.find((e) => e.target === selectedNodeId && e.targetHandle === 'output_parser')
    if (!edge) return null
    return (nodes.find((n) => n.id === edge.source)?.data as OutputParserNodeData | undefined)?.label ?? ''
  }, [nodes, edges, selectedNodeId])
  // The prompt preview is assembled by the backend from the graph on screen,
  // which includes edits autosave hasn't flushed. Sent rather than read back
  // server-side for that reason; nothing is written.
  const fetchPromptPreview = useCallback(
    (nodeId: string) =>
      protocolsApi.promptPreview(protocolId, nodeId, {
        nodes: nodes as unknown as ProtocolNode[],
        edges: edges as unknown as ProtocolEdge[],
      }),
    [protocolId, nodes, edges],
  )

  // The model each agent will actually run on, resolved through its Model
  // connector. Injected into the node's data rather than read here, because
  // whether that model can be sent function schemas needs the provider's model
  // list -- a query, which the node card subscribes to itself (see AgentNode's
  // peerNeedsToolCalling). Only the *wiring* half belongs in this file.
  const llmConfigByAgent = useMemo(() => {
    const nodeById = new Map(nodes.map((n) => [n.id, n]))
    const map = new Map<string, { provider?: string; model?: string }>()
    for (const e of edges) {
      if (e.targetHandle !== 'model') continue
      const config = (nodeById.get(e.source)?.data as ModelNodeData | undefined)?.config
      if (config) map.set(e.target, { provider: config.provider, model: config.model })
    }
    return map
  }, [nodes, edges])

  // Every node, not just the agents: the run panels list a node_run per node
  // in the graph (datasets and output parsers included -- see
  // run_protocol's own loop), and a raw uuid there names nothing the user can
  // find on the canvas. A superset is harmless for the transcript, whose
  // speaker ids are always agents.
  const nodeNames = useMemo(() => nodeDisplayNames(nodes), [nodes])

  // The experiment's declared coordination strategy, which decides what the
  // main handles MEAN -- whether a lead marker is in force, and whether the
  // main flow is capped at one edge per side. Read here rather than on the node
  // card so the card stays a pure render of what it's handed. Absent (an
  // experiment saved before the field existed) is 'sequential', matching
  // `coordination_strategy_slug` on the backend.
  const coordinationSlug = experimentQuery.data?.design_spec?.coordination_strategy?.slug ?? 'sequential'
  const isPeerCollaboration = coordinationSlug === 'peer_collaboration'
  const isSupervisor = coordinationSlug === 'supervisor_architecture'
  const isSequential = coordinationSlug === 'sequential'

  const renderedEdges = useMemo<Edge[]>(() => {
    if (!isSequential) return edges
    const agentIds = new Set(nodes.filter((node) => node.type === 'agent').map((node) => node.id))
    return edges.map((edge) => {
      const isAgentFlow =
        !edge.sourceHandle &&
        !edge.targetHandle &&
        agentIds.has(edge.source) &&
        agentIds.has(edge.target)
      if (!isAgentFlow) return edge
      return {
        ...edge,
        data: { ...edge.data, sequentialAgentFlow: true },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          width: 12,
          height: 12,
          color: 'color-mix(in oklch, var(--muted-foreground), transparent 30%)',
        },
      }
    })
  }, [edges, isSequential, nodes])

  // Which main-flow sides are already taken. Only consulted under
  // 'sequential', where the chain rule caps each side at one edge
  // (validate_sequential_chain), so the "+" stub can hide instead of offering
  // a connection the backend would reject at publish time.
  const mainEdgeSlots = useMemo(() => {
    const incoming = new Set<string>()
    const outgoing = new Set<string>()
    for (const e of edges) {
      if (CONNECTOR_HANDLES.has(e.targetHandle ?? '')) continue
      incoming.add(e.target)
      outgoing.add(e.source)
    }
    return { incoming, outgoing }
  }, [edges])

  const nodesWithRunStatus = useMemo((): Node[] => {
    return nodes.map((n) => {
      const patternHostId = patternHostIds.get(n.id)
      const isAgentLike = n.type === 'agent' || n.type === 'sub_agent'
      return {
        ...n,
        deletable: !nonDeletablePatternNodeIds.has(n.id),
        data: {
          ...n.data,
          isSubAgent: n.type === 'sub_agent',
          runStatus: latestNodeRuns?.[n.id]?.status,
          runTruncated: Boolean(latestNodeRuns?.[n.id]?.truncation),
          missingModel:
            (n.type === 'agent' || (n.type === 'sub_agent' && connectedActiveSubAgentIds.has(n.id))) &&
            !agentIdsWithModel.has(n.id),
          // "Require specific output format" is on, but nothing says what the
          // format is. Unlike missingModel this doesn't stop the run -- the agent
          // just answers in prose, which is the outcome the switch was flipped
          // to prevent, so it has to be visible on the card and not only in the
          // inspector the user has already closed. A legacy stored contract
          // counts as the answer: the executor falls back to it.
          missingOutputParser:
            isAgentLike &&
            (n.data as AgentNodeData).config?.require_output_parser === true &&
            !agentIdsWithParser.has(n.id) &&
            !(n.data as AgentNodeData).config?.output_contract,
          canRunAlone: isAgentLike && !agentIdsWithUpstream.has(n.id),
          hasPeers:
            n.type === 'agent' &&
            ((peerIdsByAgent.get(n.id)?.length ?? 0) > 0 || agentIdsWithSubAgents.has(n.id)),
          llmConfig: isAgentLike ? llmConfigByAgent.get(n.id) ?? null : null,
          // Gated on the strategy, not just the flag: a "Lead" badge left over
          // from a Peer Collaboration experiment that has since been switched
          // to Sequential would claim a role nothing acts on. The flag itself
          // is kept (see AgentNodeInspector) -- only the badge is conditional.
          // One marker, two strategies, two words for it: `conversation_lead`
          // says "starts the conversation" under Peer Collaboration and "is the
          // supervisor" under Supervisor, so the badge names the role the
          // running strategy will actually give it rather than a generic "Lead".
          leadRole:
            n.type === 'agent' && (n.data as AgentNodeData).conversation_lead === true
              ? isPeerCollaboration
                ? 'lead'
                : isSupervisor
                  ? 'supervisor'
                  : null
              : null,
          // Under Sequential the chain rule caps each main side at one edge, so
          // the "+" affordance has to match the rule rather than the rule
          // ambushing the user after they've drawn the edge.
          mainInFull: isSequential && mainEdgeSlots.incoming.has(n.id),
          mainOutFull: isSequential && mainEdgeSlots.outgoing.has(n.id),
          // Only meaningful once the pattern is actually wired to an agent --
          // an orphaned pattern node has no loop to warn about.
          // A peer is a callable capability too: both Motoro execution paths
          // append `agents_to_openai_format(available_agents)` to the function
          // payload independently of tools (engine/act.py, reason_act.py), so
          // an agent whose only capability is a peer still gets a payload and
          // the loop can run past one turn. Gated on the strategy because
          // `available_agents` is only passed under Peer Collaboration -- under
          // any other one the original warning is still exactly right.
          hostHasNoTools:
            !!patternHostId &&
            !agentIdsWithCallableTools.has(patternHostId) &&
            !(isPeerCollaboration && (peerIdsByAgent.get(patternHostId)?.length ?? 0) > 0),
          suggestedIterations: suggestedIterationsByPattern.get(n.id) ?? null,
          hostTruncation: truncationByPattern.get(n.id) ?? null,
        },
      }
    })
  }, [
    nodes,
    latestNodeRuns,
    nonDeletablePatternNodeIds,
    agentIdsWithModel,
    agentIdsWithParser,
    agentIdsWithSubAgents,
    connectedActiveSubAgentIds,
    agentIdsWithUpstream,
    agentIdsWithCallableTools,
    patternHostIds,
    peerIdsByAgent,
    suggestedIterationsByPattern,
    truncationByPattern,
    llmConfigByAgent,
    isPeerCollaboration,
    isSupervisor,
    isSequential,
    mainEdgeSlots,
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
      if (experimentLocked) return false
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
    [experimentLocked],
  )

  function resetAddPanelBrowsers() {
    setServerBrowserOpen(false)
    setSkillBrowserOpen(false)
    setBundleBrowserOpen(false)
    setDocumentBrowserOpen(false)
    setDatasetBrowserOpen(false)
  }

  function closeAddPanel() {
    setAddPanelOpen(false)
    resetAddPanelBrowsers()
    setPendingConnectorAdd(null)
    setPendingMainEdgeAdd(null)
    setPendingEdgeInsert(null)
  }

  // A connector "+" stub (ConnectorAddStub) requests this instead of
  // opening the unrestricted toolbar panel -- same panel, pre-filtered to
  // the slot's node type via CONNECTOR_PANEL_INFO, and addNode() below
  // wires the picked node straight into the requesting node's handle.
  const requestConnectorAdd = useCallback((request: ConnectorAddRequest) => {
    if (experimentLocked) return
    setSelectedNodeId(null)
    setPendingConnectorAdd(request)
    setPendingMainEdgeAdd(null)
    setPendingEdgeInsert(null)
    resetAddPanelBrowsers()
    setAddPanelOpen(true)
  }, [experimentLocked])
  // A MainEdgeAddStub requests this instead -- same panel, restricted to
  // "agent" (the only node type this stub ever creates), and addNode()
  // below wires a plain edge (no handle id) in whichever direction the
  // requesting stub sits.
  const requestMainEdgeAdd = useCallback((request: MainEdgeAddRequest) => {
    if (experimentLocked) return
    setSelectedNodeId(null)
    setPendingConnectorAdd(null)
    setPendingMainEdgeAdd(request)
    setPendingEdgeInsert(null)
    resetAddPanelBrowsers()
    setAddPanelOpen(true)
  }, [experimentLocked])
  // An InteractEdge's own "+" requests this -- same panel again, restricted
  // to "agent", and addNode() below removes the original edge and rewires
  // origin->newAgent->target instead.
  const requestEdgeInsert = useCallback((request: EdgeInsertRequest) => {
    if (experimentLocked) return
    setSelectedNodeId(null)
    setPendingConnectorAdd(null)
    setPendingMainEdgeAdd(null)
    setPendingEdgeInsert(request)
    resetAddPanelBrowsers()
    setAddPanelOpen(true)
  }, [experimentLocked])
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
      if (!experimentId || experimentLocked) return
      setAddPanelOpen(false)
      setFactorPickerNodeId(nodeId)
    },
    [experimentId, experimentLocked],
  )
  const requestEditFactor = useCallback(
    (factorName: string) => {
      if (!experimentId || experimentLocked) return
      setEditingFactorName(factorName)
    },
    [experimentId, experimentLocked],
  )
  const metricsByNode = useMemo(() => {
    const definitions = new Map((experimentQuery.data?.measurement_plan?.metrics ?? []).map((metric) => [metric.id, metric]))
    const bindings = new Map<string, Map<string, { id: string; name: string }>>()
    for (const producer of experimentQuery.data?.measurement_plan?.producers ?? []) {
      const nodeId = producer.producer_id === 'asaree.python_script'
        ? producer.config.script_node_id
        : producer.producer_id === 'asaree.mcp_tool'
          ? producer.config.mcp_node_id
          : undefined
      if (typeof nodeId !== 'string' || !nodeId) continue
      const metrics = bindings.get(nodeId) ?? new Map<string, { id: string; name: string }>()
      for (const metricId of Object.values(producer.outputs)) {
        const definition = definitions.get(metricId)
        metrics.set(metricId, { id: metricId, name: definition?.name ?? metricId })
      }
      bindings.set(nodeId, metrics)
    }
    return new Map([...bindings].map(([nodeId, metrics]) => [nodeId, [...metrics.values()]]))
  }, [experimentQuery.data?.measurement_plan])
  const metricsForNode = useCallback((nodeId: string) => metricsByNode.get(nodeId) ?? [], [metricsByNode])
  // Moves an agent's stored `config.output_contract` onto a real Output Parser
  // node -- see ProtocolCanvasContext for why this is a button rather than
  // something that happens on load. Everything lands in one pair of setNodes/
  // setEdges calls: the intermediate state (contract on the node AND on the
  // agent) is exactly the one topological_order refuses to publish.
  const convertLegacyOutputContract = useCallback(
    (nodeId: string) => {
      if (experimentLocked) return
      const agent = nodes.find((n) => n.id === nodeId)
      const contract = (agent?.data as AgentNodeData | undefined)?.config?.output_contract
      if (!agent || !contract) return
      const position = parserPositionFor(agent, nodes)
      const parserId = newNodeId()
      // The contract is copied across as-is, field types included: normalising
      // Motoro's aliases (integer -> int, and so on) here would silently
      // diverge this draft from the published revisions production runs still
      // execute. Converting changes WHERE the contract lives, nothing else.
      const parserData = defaultOutputParserNodeData()
      parserData.config.output_contract = contract
      setNodes((nds) =>
        nds
          .map((n) =>
            n.id === nodeId
              ? {
                  ...n,
                  data: {
                    ...n.data,
                    config: {
                      ...(n.data as AgentNodeData).config,
                      output_contract: null,
                      // Keeps the connector drawn once the field that was
                      // revealing it is gone.
                      require_output_parser: true,
                    },
                  },
                }
              : n,
          )
          .concat({ id: parserId, type: 'output_parser', position, data: parserData }),
      )
      setEdges((eds) =>
        eds.concat({
          id: newNodeId(),
          source: parserId,
          sourceHandle: 'output_parser',
          target: nodeId,
          targetHandle: 'output_parser',
        }),
      )
    },
    [experimentLocked, nodes, setNodes, setEdges],
  )
  const canvasActions = useMemo(
    () => ({
      requestConnectorAdd,
      requestMainEdgeAdd,
      requestEdgeInsert,
      requestRunNode,
      requestMakeFactor,
      requestEditFactor,
      metricsForNode,
      convertLegacyOutputContract,
    }),
    [
      requestConnectorAdd,
      requestMainEdgeAdd,
      requestEdgeInsert,
      requestRunNode,
      requestMakeFactor,
      requestEditFactor,
      metricsForNode,
      convertLegacyOutputContract,
    ],
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
      const fresh = await experimentsApi.get(experimentId!)
      const nextFactors = [...(fresh.design_spec?.factors ?? []).filter((candidate) => candidate.name !== factor.name), factor]
      await experimentsApi.update(experimentId!, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return { factor, field }
    },
    onSuccess: ({ factor, field }) => {
      bindFactorOnNode(field.nodeId, field.fieldPath, factor.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'design-impact'] })
    },
  })
  const editingFactor = experimentQuery.data?.design_spec?.factors?.find((factor) => factor.name === editingFactorName)
  const editFactorMutation = useMutation({
    mutationFn: async ({ oldName, next }: { oldName: string; next: DesignFactor }) => {
      const fresh = await experimentsApi.get(experimentId!)
      const nextFactors = (fresh.design_spec?.factors ?? []).map((factor) => factor.name === oldName ? next : factor)
      await experimentsApi.update(experimentId!, { design_spec: { ...fresh.design_spec, factors: nextFactors } })
      return { oldName, next }
    },
    onSuccess: ({ oldName, next }) => {
      if (next.name !== oldName) renameFactorBindings(oldName, next.name)
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId] })
      queryClient.invalidateQueries({ queryKey: ['experiments', experimentId, 'design-impact'] })
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
    if (experimentLocked) return
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
      // same x, a Tool node could land above the Model connector and every one
      // after the first got shoved onto a ring around that same point, which
      // read as nodes scattered at random rather than as "this one belongs to
      // that connector". findFreePosition then prefers the same row when the
      // spot is taken, so a second Tool sits beside the first.
      const desired = originNode
        ? {
            x: originNode.position.x + connectorNodeOffsetX(originNode.type, slot),
            y: originNode.position.y + (
              TOP_EDGE_SLOTS.has(slot) ? -160 : slot === 'sub_agents' ? SUB_AGENT_CHILD_OFFSET_Y : 160
            ),
          }
        : screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
      const position = findFreePosition(
        nodes.map((n) => n.position),
        desired,
        slot === 'sub_agents' ? { width: 320, height: 140 } : CONNECTOR_CHILD_CLEARANCE,
      )
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
      const connectorNode: Node = { id: newId, type: nodeType, position, data: dataOverride ?? defaultDataFor(nodeType) }
      const ownerEdge: Edge = { id: newNodeId(), source: newId, sourceHandle: slot, target: originId, targetHandle: slot }
      if (nodeType === 'sub_agent') {
        const { patternNode, patternEdge } = agentDefaultPattern(newId, position, nodes.map((n) => n.position))
        setNodes((nds) => nds.concat(connectorNode, patternNode))
        setEdges((eds) => eds.concat(ownerEdge, patternEdge))
      } else {
        setNodes((nds) =>
          nds
            .filter((n) => n.id !== existingPatternEdge?.source)
            .concat(connectorNode),
        )
        setEdges((eds) =>
          eds
            .filter((e) => e.id !== existingPatternEdge?.id)
            .concat(ownerEdge),
        )
      }
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

    // An Output Parser is the one catalog entry that is meaningless on its
    // own: it has exactly one legal connection, it configures the agent it
    // hangs off, and dropped loose it silently does nothing while looking like
    // it is set up. Picking it from the connector's own "+" already wires it
    // (the pendingConnectorAdd branch above); picking it from the unrestricted
    // toolbar panel did not, which is the whole gap.
    //
    // Wired only when the host is unambiguous. An agent that has "Require
    // specific output format" on and nothing answering it is asking for this
    // node by name, so it wins outright; failing that, a canvas with a single
    // parser-less agent has only one place the node could go. Two candidates
    // and it stays loose rather than attaching to a guess -- the user drags the
    // edge, which is the same work as correcting a wrong one.
    if (nodeType === 'output_parser') {
      const parserless = nodes.filter(
        (n) => (n.type === 'agent' || n.type === 'sub_agent') && !edges.some((e) => e.target === n.id && e.targetHandle === 'output_parser'),
      )
      const asking = parserless.filter((n) => (n.data as AgentNodeData).config?.require_output_parser === true)
      const host = (asking.length === 1 ? asking : parserless.length === 1 ? parserless : [])[0]
      if (host) {
        setNodes((nds) => nds.concat({ ...newNode, position: parserPositionFor(host, nds) }))
        setEdges((eds) =>
          eds.concat({
            id: newNodeId(),
            source: newId,
            sourceHandle: 'output_parser',
            target: host.id,
            targetHandle: 'output_parser',
          }),
        )
        setAddPanelOpen(false)
        setSelectedNodeId(newId)
        return
      }
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
          queryClient.setQueryData<Protocol>(protocolForExperimentQueryKey(updated.experiment_id), (previous) =>
            mergeProtocolSaveIntoCache(previous, updated),
          )
          queryClient.invalidateQueries({ queryKey: ['experiments', updated.experiment_id, 'design-impact'] })
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
  const factors = experimentQuery.data?.design_spec?.factors ?? EMPTY_FACTORS
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
  // Model/Tool/Memory node's plain label alone doesn't say which agent it
  // belongs to (see bindableFields.ts's own comment), so every inspector
  // that wraps a field in "+ Make experimental factor" gets this instead of
  // data.label for that purpose specifically; the header title itself still
  // shows the node's own plain label, unaffected.
  const factorNodeLabel = selectedNode ? agentTracedLabel(selectedNode, edges, nodes) : ''
  // A factor only has meaning while it controls at least one canvas field.
  // Whether the final binding was explicitly unbound in an inspector or was
  // removed with a deleted node, remove that factor declaration too so the
  // Design panel and the materialized matrix cannot advertise a treatment
  // the canvas no longer has.
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

  function removeFactorsForDeletedNodes(deleting: Node[], nextNodes: Node[]) {
    const removedNames = [...new Set(
      deleting.flatMap((node) => Object.values((node.data as { factor_bindings?: Record<string, string> }).factor_bindings ?? {})),
    )]
    void removeUnboundFactors(nextNodes, removedNames)
  }

  function updateNodeData(
    nodeId: string,
    data:
      | AgentNodeData
      | McpToolNodeData
      | CriticGateNodeData
      | ModelNodeData
      | MemoryNodeData
      | OutputParserNodeData
      | DatasetNodeData
      | SkillNodeData
      | OkfBundleNodeData
      | OkfDocumentNodeData
      | ScriptNodeData
      | ReasonActPatternNodeData
      | SingleAgentBaselinePatternNodeData,
  ) {
    if (experimentLocked) return
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
      const targetIsAgentLike = targetNode.type === 'agent' || targetNode.type === 'sub_agent'
      switch (connection.targetHandle) {
        case 'model':
          return (
            MODEL_NODE_TYPES.includes(sourceNode.type ?? '') &&
            (targetIsAgentLike || targetNode.type === 'critic_gate')
          )
        case 'tool':
          return (
            (MCP_TOOL_NODE_TYPES.includes(sourceNode.type ?? '') || sourceNode.type === 'script') &&
            targetIsAgentLike
          )
        case 'memory':
          return sourceNode.type === 'memory' && targetIsAgentLike
        case 'output_parser':
          // Agent only, deliberately not critic_gate: a gate's answer is a
          // pass/fail decision the executor already reads structurally, so
          // there is nothing for a contract to extract.
          return sourceNode.type === 'output_parser' && targetIsAgentLike
        case 'architectural_pattern':
          return PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') && targetIsAgentLike
        case 'skill':
          return sourceNode.type === 'skill' && targetIsAgentLike
        case 'dataset':
          // Uncapped: a cell's workspace holds one dataset per named SLOT, so
          // several datasets on one agent is a supported shape, not a run-time
          // error (see AgentNode.tsx's Dataset comment). Wiring order is the
          // order the agent's prompt lists the slots in. Note this is still
          // distinct from COMPARING datasets across cells, which is a
          // 'dataset_config' factor.
          return sourceNode.type === 'dataset' && targetIsAgentLike
        case 'knowledge':
          // The one connector with two source types -- bundles and uploaded
          // documents are interchangeable here, since both resolve to the same
          // per-directory OKF server.
          return KNOWLEDGE_NODE_TYPES.includes(sourceNode.type ?? '') && targetIsAgentLike
        case 'sub_agents':
          return (
            sourceNode.type === 'sub_agent' &&
            targetNode.type === 'agent' &&
            !edges.some((edge) => edge.source === sourceNode.id && edge.targetHandle === 'sub_agents')
          )
        default: {
          // A plain "main" pipeline edge -- LLM/memory/pattern/mcp_tool/
          // dataset/skill/knowledge/script nodes have no main handle to drag from in
          // the first place, so this mostly guards against a stray
          // connection, not real interactive use.
          const sourceCanFeedMainFlow =
            !MODEL_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'memory' &&
            sourceNode.type !== 'output_parser' &&
            !MCP_TOOL_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'dataset' &&
            sourceNode.type !== 'skill' &&
            !KNOWLEDGE_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'script' &&
            !PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'sub_agent' &&
            targetNode.type !== 'sub_agent'
          if (!sourceCanFeedMainFlow) return false
          // Under Sequential the main flow is a chain: one edge out of each
          // node, one into each. Enforced here so the canvas refuses the fork
          // as you draw it, rather than validate_sequential_chain rejecting the
          // whole protocol at publish time. Other strategies leave the main
          // flow unrestricted -- a fork is exactly the shape Peer Collaboration
          // exists for, and a Critic Gate pipeline routes around agents.
          if (!isSequential) return true
          return (
            !edges.some((e) => e.source === connection.source && !CONNECTOR_HANDLES.has(e.targetHandle ?? '')) &&
            !edges.some((e) => e.target === connection.target && !CONNECTOR_HANDLES.has(e.targetHandle ?? ''))
          )
        }
      }
    },
    [nodes, edges, isSequential],
  )

  function deleteNode(nodeId: string) {
    if (experimentLocked) return
    const deleting = nodes.filter((node) => node.id === nodeId)
    const nextNodes = nodes.filter((node) => node.id !== nodeId)
    setNodes(nextNodes)
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId))
    setSelectedNodeId(null)
    removeFactorsForDeletedNodes(deleting, nextNodes)
  }

  // The node inspector's own Delete button calls deleteNode directly --
  // never through xyflow's deleteElements, so onBeforeDelete never sees it.
  // Shows the same confirmation dialog either way (no `resolve`, since
  // there's no pending xyflow deletion to approve/reject here -- just call
  // deleteNode for real once the user confirms).
  function requestDeleteNode(nodeId: string) {
    if (experimentLocked) return
    const node = nodes.find((n) => n.id === nodeId)
    if (!node) return
    const relatedEdges = edges.filter((e) => e.source === nodeId || e.target === nodeId)
    setPendingDelete({ nodes: [node], edges: relatedEdges })
  }

  return (
    <ProtocolCanvasActionsProvider value={canvasActions}>
      <div className="flex h-full w-full">
        <div ref={paneRef} className="relative flex-1">
          <ReactFlow
            nodes={nodesWithRunStatus}
            edges={renderedEdges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            nodesDraggable={!experimentLocked}
            nodesConnectable={!experimentLocked}
            edgesReconnectable={!experimentLocked}
            isValidConnection={isValidConnection}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            onBeforeDelete={onBeforeDelete}
            onNodeClick={() => setAddPanelOpen(false)}
            onNodeDoubleClick={(_, node) => {
              if (experimentLocked) return
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
          {!experimentLocked && <CanvasControls onTidy={tidyUp} />}
          {(() => {
            // testRunMutation.error/runNodeMutation.error is the real validation
            // message (e.g. topological_order/validate_single_node_runnable
            // rejecting before any ProtocolRun row even exists) --
            // runQuery.data?.error only ever exists once a run row was
            // created and later failed asynchronously in the worker.
            const failedMutation = testRunMutation.isError ? testRunMutation : runNodeMutation.isError ? runNodeMutation : null
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
            {/* There is deliberately no separate "start a conversation"
                button here. Whether connected agents collaborate is the
                experiment's coordination strategy (Design tab), not a second
                way to press Run -- so an agent conversation is started by
                running the protocol, like everything else. */}
            {/* Always opens RunConfirmDialog rather than firing a real,
                billable run on one click. That dialog does its own pre-flight
                scan for obviously misconfigured nodes (no model, no dataset
                picked, no script code, an agent with nothing wired into its
                required Model connector) and surfaces them inline, instead of
                the user only finding out via a generic "one or more nodes
                failed" AFTER paying for the attempt. It also owns the
                publish-then-run choice when the draft differs from the
                published revision. */}
            <Button
              size="sm"
              // Not disabled by `experimentLocked`: locking freezes the
              // design, which is precisely when you want to collect data, and
              // the backend agrees -- the lock guards sit on update/publish/
              // delete, never on creating a run.
              disabled={isRunning}
              onClick={() => {
                setRunErrorDismissed(false)
                setPendingRunConfirm({ type: 'graph' })
              }}
              title="Start a Test Run for this canvas, with no factor values substituted in"
            >
              <Play className="size-4" />
              {isRunning ? 'Test Run running…' : 'Test Run'}
            </Button>
            {testRunQuery.data && !testResultsOpen && (
              <ReopenTestRunResultsButton onOpen={() => setTestResultsOpen(true)} refresh={() => { testRunQuery.refetch() }} />
            )}
            {playResult && !playResultsOpen && (
              <ReopenTestRunResultsButton label="Play Results" onOpen={() => setPlayResultsOpen(true)} refresh={() => { runQuery.refetch() }} />
            )}
            <Button
              size="icon"
              className="rounded-full"
              aria-label="Add node"
              disabled={experimentLocked}
              onClick={() => {
                setSelectedNodeId(null)
                setPendingConnectorAdd(null)
                setPendingMainEdgeAdd(null)
                setPendingEdgeInsert(null)
                resetAddPanelBrowsers()
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
            />
          </div>
          {testResultsOpen && testRunQuery.data && (
            <TestRunResults run={testRunQuery.data} nodeNames={nodeNames} onClose={() => setTestResultsOpen(false)} />
          )}
          {playResultsOpen && playResult && (
            <TestRunResults title="Play Results" run={playResult} nodeNames={nodeNames} onClose={() => setPlayResultsOpen(false)} />
          )}
          {/* One top-left column rather than two independently-positioned
              overlays: the lock badge and the transcript are both anchored
              here, and stacking them is what keeps them from landing on top of
              each other. `items-start` so each stays its own natural width.
              Top-LEFT because bottom-right is the MiniMap's corner and
              top-right is the Add/menu buttons'. The column itself is
              `pointer-events-none` so the empty space it reserves stays part of
              the canvas -- panning and node drags must still work under it --
              and each child turns events back on for itself. */}
          {(experimentLocked || showStandaloneConversation) && (
            <div className="pointer-events-none absolute top-3 left-3 z-10 flex max-h-[55%] w-[min(28rem,calc(100%-1.5rem))] flex-col items-start gap-2">
              {experimentLocked && (
                <div className="pointer-events-auto inline-flex shrink-0 items-center gap-1.5 rounded-md border border-primary/30 bg-background/95 px-2.5 py-1.5 text-xs font-medium shadow-sm">
                  <Lock className="size-3.5" /> Canvas locked
                </div>
              )}
              {showStandaloneConversation && runQuery.data?.conversation && (
                <ConversationTranscript conversation={runQuery.data.conversation} agentNames={nodeNames} />
              )}
            </div>
          )}
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
            nodeRun={latestNodeRuns?.[selectedNode.id]}
            onChange={updateNodeData}
            onDelete={requestDeleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : MODEL_NODE_TYPES.includes(selectedNode?.type ?? '') ? (
          <ModelNodeInspector
            node={{ id: selectedNode!.id, type: selectedNode!.type!, position: selectedNode!.position, data: selectedNode!.data as ModelNodeData }}
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
        ) : selectedNode?.type === 'output_parser' ? (
          <OutputParserNodeInspector
            node={{ id: selectedNode.id, type: 'output_parser', position: selectedNode.position, data: selectedNode.data as OutputParserNodeData }}
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
            suggestedIterations={suggestedIterationsByPattern.get(selectedNode.id) ?? null}
            truncatedAt={truncationByPattern.get(selectedNode.id)?.max_iterations ?? null}
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
              markedLeadAgentId={markedLeadAgentId}
              referenceScope={referenceScope}
              handoffPeers={selectedHandoffPeers}
              wiredOutputParserLabel={selectedOutputParserLabel}
              fetchPromptPreview={fetchPromptPreview}
              nodeRun={latestNodeRuns?.[selectedNode.id]}
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
              const deletedNodeIds = new Set(pendingDelete.nodes.map((node) => node.id))
              removeFactorsForDeletedNodes(
                pendingDelete.nodes,
                nodes.filter((node) => !deletedNodeIds.has(node.id)),
              )
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
          truncatedCaps={truncatedCaps}
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
          confirmLabel={pendingRunConfirm.type === 'graph' ? 'Start Test Run' : undefined}
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
          promptScopeFor={promptScopeFor}
          onSave={(factor, field) => {
            if (field) return createFactorMutation.mutateAsync({ factor, field })
          }}
        />
      )}
      {editingFactor && (
        <FactorEditorDialog
          open
          onOpenChange={(open) => {
            if (!open) setEditingFactorName(null)
          }}
          factor={editingFactor}
          toolServerId={toolFactorServerId(nodes, editingFactor.name)}
          revealHiddenServers={revealsHiddenMcpServers(nodes)}
          boundField={factorBoundField(nodes, editingFactor.name)}
          promptScopeFor={promptScopeFor}
          onSave={(next) => editFactorMutation.mutateAsync({ oldName: editingFactor.name, next })}
        />
      )}
    </ProtocolCanvasActionsProvider>
  )
})
