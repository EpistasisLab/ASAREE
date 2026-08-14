import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
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
import { Play, Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { protocolsApi } from '@/api/client'
import { newNodeId } from '@/lib/nodeId'
import { toPersistedGraph } from '@/lib/protocolGraph'
import { TERMINAL_RUN_STATUSES } from '@/lib/protocolRun'
import {
  defaultAgentNodeData,
  defaultAnthropicLlmNodeData,
  defaultAzureFoundryLlmNodeData,
  defaultCriticGateNodeData,
  defaultMcpToolNodeData,
  defaultMemoryNodeData,
  defaultOpenAiLlmNodeData,
  defaultReasonActPatternNodeData,
  defaultSingleAgentBaselinePatternNodeData,
} from '@/types/protocols'
import type {
  AgentNodeData,
  CriticGateNodeData,
  LlmNodeData,
  McpToolNodeData,
  MemoryNodeData,
  ProtocolGraph,
  ProtocolNode,
  ReasonActPatternNodeData,
  SingleAgentBaselinePatternNodeData,
} from '@/types/protocols'
import { AddNodePanel } from './AddNodePanel'
import { AgentNodeInspector } from './AgentNodeInspector'
import { CanvasControls } from './CanvasControls'
import { CriticGateNodeInspector } from './CriticGateNodeInspector'
import { DEFAULT_ZOOM } from './constants'
import { findFreePosition } from './layout'
import { LlmNodeInspector } from './LlmNodeInspector'
import { McpToolNodeInspector } from './McpToolNodeInspector'
import { MemoryNodeInspector } from './MemoryNodeInspector'
import { ProtocolCanvasActionsProvider, type ConnectorAddRequest, type ConnectorSlot } from './ProtocolCanvasContext'
import { ReasonActPatternNodeInspector } from './ReasonActPatternNodeInspector'
import { SingleAgentBaselinePatternNodeInspector } from './SingleAgentBaselinePatternNodeInspector'
import { AgentNode } from './nodes/AgentNode'
import { CriticGateNode } from './nodes/CriticGateNode'
import { LlmNode } from './nodes/LlmNode'
import { McpToolNode } from './nodes/McpToolNode'
import { MemoryNode } from './nodes/MemoryNode'
import { ReasonActPatternNode } from './nodes/ReasonActPatternNode'
import { SingleAgentBaselinePatternNode } from './nodes/SingleAgentBaselinePatternNode'
import { ProtocolCanvasMenu } from './ProtocolCanvasMenu'

// One node type per LLM provider / architectural pattern (see LlmNodeData/
// ReasonActPatternNodeData's own comments in types/protocols.ts) -- each
// connector slot accepts this whole family, not one exact type, mirroring
// how the "tool" slot already accepts any mcp_tool node.
const LLM_NODE_TYPES = ['llm_anthropic', 'llm_openai', 'llm_azure_foundry']
const PATTERN_NODE_TYPES = ['pattern_reason_act', 'pattern_single_agent_baseline']

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
  pattern_reason_act: ReasonActPatternNode,
  pattern_single_agent_baseline: SingleAgentBaselinePatternNode,
}
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
  if (nodeType === 'pattern_reason_act') return defaultReasonActPatternNodeData()
  if (nodeType === 'pattern_single_agent_baseline') return defaultSingleAgentBaselinePatternNodeData()
  return defaultAgentNodeData()
}

// Mirrors isValidConnection's own per-slot source-type-family rule -- the
// panel that opens for a connector "+" is pre-filtered to that slot's whole
// family of node types (LLM_NODE_TYPES/PATTERN_NODE_TYPES above) rather than
// the full catalog.
const CONNECTOR_PANEL_INFO: Record<ConnectorSlot, { allowedTypes: string[]; title: string }> = {
  llm: { allowedTypes: LLM_NODE_TYPES, title: 'Add LLM' },
  tool: { allowedTypes: ['mcp_tool'], title: 'Add Tool' },
  memory: { allowedTypes: ['memory'], title: 'Add Memory' },
  architectural_pattern: { allowedTypes: PATTERN_NODE_TYPES, title: 'Add Architectural Pattern' },
}

export function ProtocolCanvas({
  protocolId,
  experimentId,
  initialGraph,
}: {
  protocolId: string
  experimentId: string | null
  initialGraph: ProtocolGraph
}) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialGraph.nodes as Node[])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialGraph.edges as Edge[])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [addPanelOpen, setAddPanelOpen] = useState(false)
  // Set only while the "+" panel was opened via a connector stub (as
  // opposed to the canvas's own unrestricted toolbar "+") -- addNode()
  // branches on this to wire the new node into the requesting node's slot
  // and open its Inspector immediately, instead of dropping it unconnected
  // near the viewport center.
  const [pendingConnectorAdd, setPendingConnectorAdd] = useState<ConnectorAddRequest | null>(null)
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

  const nodesWithRunStatus = useMemo(() => {
    if (!runQuery.data) return nodes
    return nodes.map((n) => ({
      ...n,
      data: { ...n.data, runStatus: runQuery.data!.node_runs[n.id]?.status },
    }))
  }, [nodes, runQuery.data])

  function closeAddPanel() {
    setAddPanelOpen(false)
    setPendingConnectorAdd(null)
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
  const canvasActions = useMemo(() => ({ requestConnectorAdd }), [requestConnectorAdd])

  // n8n's own pattern: a "+" on the canvas opens a searchable node-type
  // panel on the right, rather than a static always-visible drag palette.
  // New nodes land near the pane's current center, nudged away from any
  // node already there (findFreePosition) so a fresh node never lands on
  // top of an existing one.
  function addNode(nodeType: string) {
    if (pendingConnectorAdd) {
      const { nodeId: originId, slot } = pendingConnectorAdd
      const originNode = nodes.find((n) => n.id === originId)
      const desired = originNode
        ? { x: originNode.position.x, y: originNode.position.y + 160 }
        : screenToFlowPosition({ x: window.innerWidth / 2, y: window.innerHeight / 2 })
      const position = findFreePosition(nodes.map((n) => n.position), desired)
      const newId = newNodeId()
      setNodes((nds) => nds.concat({ id: newId, type: nodeType, position, data: defaultDataFor(nodeType) }))
      setEdges((eds) => eds.concat({ id: newNodeId(), source: newId, sourceHandle: slot, target: originId, targetHandle: slot }))
      setPendingConnectorAdd(null)
      setAddPanelOpen(false)
      // Mirrors n8n's own flow: picking a node from the connector panel
      // goes straight into that node's Inspector to set it up, rather than
      // leaving the user to double-click it themselves.
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
      // The execution pattern's default (agentic-core's own "reason_act",
      // see _resolve_pattern_config) deliberately never stays invisible --
      // unlike LLM (no auto-created default; you must wire one), every new
      // Agent gets an explicit, real "Reason + Act" node created and wired
      // in immediately. Delete it (or swap it for Single-Agent Baseline) to
      // opt out/change it -- the connector itself stays optional.
      const patternId = newNodeId()
      const patternPosition = findFreePosition(
        [...nodes.map((n) => n.position), position],
        { x: position.x, y: position.y + 160 },
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
        target: newId,
        targetHandle: 'architectural_pattern',
      }
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

  function updateNodeData(
    nodeId: string,
    data:
      | AgentNodeData
      | McpToolNodeData
      | CriticGateNodeData
      | LlmNodeData
      | MemoryNodeData
      | ReasonActPatternNodeData
      | SingleAgentBaselinePatternNodeData,
  ) {
    setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data } : n)))
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
          return sourceNode.type === 'mcp_tool' && targetNode.type === 'agent'
        case 'memory':
          return sourceNode.type === 'memory' && targetNode.type === 'agent'
        case 'architectural_pattern':
          return PATTERN_NODE_TYPES.includes(sourceNode.type ?? '') && targetNode.type === 'agent'
        default:
          // A plain "main" pipeline edge -- LLM/memory/pattern nodes have
          // no main handle to drag from in the first place, so this mostly
          // guards against a stray connection, not real interactive use.
          return (
            !LLM_NODE_TYPES.includes(sourceNode.type ?? '') &&
            sourceNode.type !== 'memory' &&
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
          <div className="absolute top-3 right-3 z-10 flex items-center gap-2">
            {(runMutation.isError || runQuery.data?.status === 'failed') && (
              <span className="max-w-64 truncate rounded-md bg-destructive/10 px-2 py-1 text-xs text-destructive" title={runQuery.data?.error ?? undefined}>
                {runQuery.data?.error ?? 'Could not start the run.'}
              </span>
            )}
            <Button size="sm" disabled={isRunning} onClick={() => runMutation.mutate()}>
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
            allowedTypes={pendingConnectorAdd ? CONNECTOR_PANEL_INFO[pendingConnectorAdd.slot].allowedTypes : undefined}
            title={pendingConnectorAdd ? CONNECTOR_PANEL_INFO[pendingConnectorAdd.slot].title : undefined}
          />
        ) : selectedNode?.type === 'mcp_tool' ? (
          <McpToolNodeInspector
            node={{ id: selectedNode.id, type: 'mcp_tool', position: selectedNode.position, data: selectedNode.data as McpToolNodeData }}
            onChange={updateNodeData}
            onDelete={deleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'critic_gate' ? (
          <CriticGateNodeInspector
            node={{ id: selectedNode.id, type: 'critic_gate', position: selectedNode.position, data: selectedNode.data as CriticGateNodeData }}
            experimentId={experimentId}
            onChange={updateNodeData}
            onDelete={deleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : LLM_NODE_TYPES.includes(selectedNode?.type ?? '') ? (
          <LlmNodeInspector
            node={{ id: selectedNode!.id, type: selectedNode!.type!, position: selectedNode!.position, data: selectedNode!.data as LlmNodeData }}
            experimentId={experimentId}
            onChange={updateNodeData}
            onDelete={deleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : selectedNode?.type === 'memory' ? (
          <MemoryNodeInspector
            node={{ id: selectedNode.id, type: 'memory', position: selectedNode.position, data: selectedNode.data as MemoryNodeData }}
            onChange={updateNodeData}
            onDelete={deleteNode}
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
            onChange={updateNodeData}
            onDelete={deleteNode}
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
            onChange={updateNodeData}
            onDelete={deleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        ) : (
          selectedNode && (
            <AgentNodeInspector
              node={{ id: selectedNode.id, type: selectedNode.type ?? 'agent', position: selectedNode.position, data: selectedNode.data as AgentNodeData }}
              experimentId={experimentId}
              onChange={updateNodeData}
              onDelete={deleteNode}
              onClose={() => setSelectedNodeId(null)}
            />
          )
        )}
      </div>
    </ProtocolCanvasActionsProvider>
  )
}
