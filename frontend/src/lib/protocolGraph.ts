import type { Edge, Node } from '@xyflow/react'
import type { AgentNodeData, CriticGateNodeData, McpToolNodeData, ProtocolGraph } from '@/types/protocols'

// Only durable fields are persisted -- xyflow annotates nodes/edges with
// ephemeral UI state (selected, dragging, measured dimensions) that has no
// meaning once reloaded from the backend. Shared by ProtocolCanvas.tsx's own
// autosave and ProtocolCanvasMenu.tsx's Download action, which both need to
// serialize the canvas's live in-memory state the same way -- kept in its
// own module (not exported alongside a component) so both files stay
// Fast-Refresh-friendly.
export function toPersistedGraph(nodes: Node[], edges: Edge[]): ProtocolGraph {
  return {
    nodes: nodes.map((n) => ({
      id: n.id,
      type: n.type ?? 'agent',
      position: n.position,
      data: n.data as AgentNodeData | McpToolNodeData | CriticGateNodeData,
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
