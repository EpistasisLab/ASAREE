import { useCallback, useEffect, useRef, useState } from 'react'
import {
  addEdge,
  Background,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
  useReactFlow,
  type Connection,
  type Edge,
  type Node,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { Plus } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { protocolsApi } from '@/api/client'
import { defaultAgentNodeData, defaultMcpToolNodeData } from '@/types/protocols'
import type { AgentNodeData, McpToolNodeData, ProtocolGraph, ProtocolNode } from '@/types/protocols'
import { AddNodePanel } from './AddNodePanel'
import { AgentNodeInspector } from './AgentNodeInspector'
import { CanvasControls } from './CanvasControls'
import { McpToolNodeInspector } from './McpToolNodeInspector'
import { AgentNode } from './nodes/AgentNode'
import { McpToolNode } from './nodes/McpToolNode'

const NODE_TYPES = { agent: AgentNode, mcp_tool: McpToolNode }
const AUTOSAVE_DELAY_MS = 800

function defaultDataFor(nodeType: string): ProtocolNode['data'] {
  return nodeType === 'mcp_tool' ? defaultMcpToolNodeData('New MCP Tool') : defaultAgentNodeData('New Agent')
}

// crypto.randomUUID() only exists in a secure context (HTTPS or localhost) --
// throws in plain-HTTP dev setups reached via a LAN IP/forwarded hostname,
// which silently aborted addNode before setNodes ever ran. This has no such
// restriction.
function newNodeId(): string {
  return `node-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`
}

// Only durable fields are persisted -- xyflow annotates nodes/edges with
// ephemeral UI state (selected, dragging, measured dimensions) that has no
// meaning once reloaded from the backend.
function toPersistedGraph(nodes: Node[], edges: Edge[]): ProtocolGraph {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type ?? 'agent',
      position: n.position,
      data: n.data as AgentNodeData | McpToolNodeData,
    })),
    edges: edges.map((e) => ({
      id: e.id,
      source: e.source,
      target: e.target,
      sourceHandle: e.sourceHandle,
      targetHandle: e.targetHandle,
    })),
  }
}

export function ProtocolCanvas({ protocolId, initialGraph }: { protocolId: string; initialGraph: ProtocolGraph }) {
  const [nodes, setNodes, onNodesChange] = useNodesState<Node>(initialGraph.nodes as Node[])
  const [edges, setEdges, onEdgesChange] = useEdgesState<Edge>(initialGraph.edges as Edge[])
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [addPanelOpen, setAddPanelOpen] = useState(false)
  const paneRef = useRef<HTMLDivElement>(null)
  const { screenToFlowPosition } = useReactFlow()

  const onConnect = useCallback((connection: Connection) => setEdges((eds) => addEdge(connection, eds)), [setEdges])

  // n8n's own pattern: a "+" on the canvas opens a searchable node-type
  // panel on the right, rather than a static always-visible drag palette.
  // New nodes land near the pane's current center, with a little jitter so
  // repeated adds don't stack exactly on top of each other.
  function addNode(nodeType: string) {
    const rect = paneRef.current?.getBoundingClientRect()
    const center = rect
      ? { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }
      : { x: window.innerWidth / 2, y: window.innerHeight / 2 }
    const jitter = () => (Math.random() - 0.5) * 80
    const position = screenToFlowPosition({ x: center.x + jitter(), y: center.y + jitter() })
    setNodes((nds) => nds.concat({ id: newNodeId(), type: nodeType, position, data: defaultDataFor(nodeType) }))
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

  function updateNodeData(nodeId: string, data: AgentNodeData | McpToolNodeData) {
    setNodes((nds) => nds.map((n) => (n.id === nodeId ? { ...n, data } : n)))
  }

  function deleteNode(nodeId: string) {
    setNodes((nds) => nds.filter((n) => n.id !== nodeId))
    setEdges((eds) => eds.filter((e) => e.source !== nodeId && e.target !== nodeId))
    setSelectedNodeId(null)
  }

  return (
    <div className="flex h-full w-full">
      <div ref={paneRef} className="relative flex-1">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
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
          proOptions={{ hideAttribution: true }}
        >
          <Background color="var(--primary)" gap={28} size={1} style={{ opacity: 0.2 }} />
          <MiniMap pannable zoomable className="!bg-card" maskColor="color-mix(in oklch, var(--background), transparent 40%)" />
        </ReactFlow>
        <CanvasControls />
        <Button
          size="icon"
          className="absolute top-3 right-3 z-10 rounded-full"
          aria-label="Add node"
          onClick={() => {
            setSelectedNodeId(null)
            setAddPanelOpen(true)
          }}
        >
          <Plus className="size-4" />
        </Button>
      </div>
      {addPanelOpen ? (
        <AddNodePanel onAdd={addNode} onClose={() => setAddPanelOpen(false)} />
      ) : selectedNode?.type === 'mcp_tool' ? (
        <McpToolNodeInspector
          node={{ id: selectedNode.id, type: 'mcp_tool', position: selectedNode.position, data: selectedNode.data as McpToolNodeData }}
          onChange={updateNodeData}
          onDelete={deleteNode}
          onClose={() => setSelectedNodeId(null)}
        />
      ) : (
        selectedNode && (
          <AgentNodeInspector
            node={{ id: selectedNode.id, type: selectedNode.type ?? 'agent', position: selectedNode.position, data: selectedNode.data as AgentNodeData }}
            onChange={updateNodeData}
            onDelete={deleteNode}
            onClose={() => setSelectedNodeId(null)}
          />
        )
      )}
    </div>
  )
}
