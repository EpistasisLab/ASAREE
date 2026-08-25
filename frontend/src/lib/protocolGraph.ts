import type { QueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import type { AgentNodeData, CriticGateNodeData, McpToolNodeData, Protocol, ProtocolGraph } from '@/types/protocols'

// The name the canvas gives a protocol it auto-creates for an experiment.
// The "[shortid]" suffix is load-bearing: protocol names are unique per owner
// (uq_protocols_owner_name) and two experiments sharing a name is the normal
// case (every new one starts as "Untitled Experiment"), so a plain name would
// 409 forever. Mirrors the server's services/protocols.py
// generated_protocol_name -- change both together.
export function generatedProtocolName(experimentName: string, experimentId: string) {
  return `Protocol: ${experimentName} [${experimentId.slice(0, 8)}]`
}

// Whether a protocol still carries its auto-generated name (matched by shape,
// so a protocol created under an older experiment name still counts) rather
// than one a user typed deliberately, which must never be overwritten.
export function isGeneratedProtocolName(name: string, experimentId: string) {
  return new RegExp(`^Protocol: .*\\[${experimentId.slice(0, 8)}\\]$`).test(name ?? '')
}

// Renaming an experiment re-syncs its auto-named protocols server-side (see
// the PATCH /experiments/{id} handler); this applies the same rename to the
// cached protocol row so the canvas doesn't keep showing the old name until
// the next reload. Written with setQueryData rather than invalidateQueries on
// purpose: a refetch of this key re-seeds the canvas's nodes/edges, which can
// race a still-in-flight autosave (see protocolForExperimentQueryKey above).
export function applyExperimentRenameToProtocolCache(
  queryClient: QueryClient,
  experimentId: string,
  experimentName: string,
) {
  queryClient.setQueryData(protocolForExperimentQueryKey(experimentId), (prev: Protocol | undefined) =>
    prev && isGeneratedProtocolName(prev.name, experimentId)
      ? { ...prev, name: generatedProtocolName(experimentName, experimentId) }
      : prev,
  )
}

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
