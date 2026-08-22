import type { Edge, Node } from '@xyflow/react'
import type { AgentNodeData, CriticGateNodeData, McpToolNodeData, ProtocolGraph } from '@/types/protocols'

// The shared react-query key ProtocolCanvas.tsx mirrors its own live
// nodes/edges into on every change (no debounce -- a pure in-memory cache
// write, not a network call) and DesignTab.tsx reads from -- this is how
// the Design tab's "Add factor" picker sees the canvas's current state
// without waiting for the 800ms autosave round-trip, and without either
// side needing to know about the other's internals. Kept in one place so
// both sides can never drift onto different keys by accident.
export function protocolGraphQueryKey(protocolId: string) {
  return ['protocol-graph', protocolId] as const
}

// The key for the *persisted* protocol row ProtocolCanvasPage.tsx fetches
// and seeds the canvas's initial nodes/edges from. Shared here because
// ProtocolCanvas.tsx's autosave has to push every saved graph back into
// this cache entry: navigating away unmounts the canvas, so the next visit
// re-seeds itself from whatever this cache holds, and a cache still holding
// the pre-edit graph makes a just-added node reappear as missing.
export function protocolForExperimentQueryKey(experimentId: string) {
  return ['protocols', 'for-experiment', experimentId] as const
}

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
