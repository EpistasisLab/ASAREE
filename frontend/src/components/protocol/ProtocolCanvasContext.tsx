import { createContext, useContext } from 'react'

export type ConnectorSlot = 'ai' | 'tool' | 'memory' | 'architectural_pattern' | 'resource' | 'skill'

// What each slot is CALLED in the UI, as opposed to the handle id it's
// stored under -- the multi-word ones would otherwise surface raw,
// snake_cased, in the tooltip/aria-label on their own "+" stub. Keep in
// sync with the captions AgentNode/CriticGateNode render next to each
// handle.
export const CONNECTOR_SLOT_LABELS: Record<ConnectorSlot, string> = {
  ai: 'AI',
  tool: 'Tool',
  memory: 'Memory',
  architectural_pattern: 'Architectural Pattern',
  resource: 'Resource',
  skill: 'Skill',
}

export interface ConnectorAddRequest {
  nodeId: string
  slot: ConnectorSlot
}

// The main pipeline handle's own add-and-wire request -- "agents can
// interact" (not a strict handoff), so this is plain edge with no handle
// id, in whichever direction the requesting stub sits: "outgoing" wires
// nodeId -> the new agent, "incoming" wires the new agent -> nodeId. Both
// are unrestricted fan-out/fan-in (no cap), unlike every named connector
// slot above.
export interface MainEdgeAddRequest {
  nodeId: string
  direction: 'incoming' | 'outgoing'
}

// InteractEdge's own "+" (hover toolbar on a plain main edge) -- splits an
// existing A->B edge into A->newAgent->B, same "always an Agent" scope as
// MainEdgeAddRequest.
export interface EdgeInsertRequest {
  edgeId: string
  source: string
  target: string
}

interface ProtocolCanvasActions {
  // Opens the "+" side panel filtered to whatever node type(s) fill this
  // slot (today always exactly one per slot, but the panel-based flow
  // is deliberately a picker rather than instant-creating, so this
  // generalizes for free once a slot ever offers more than one kind of
  // node). Selecting one creates it near the requesting node, wires the
  // edge into the right handle, and opens its Inspector immediately.
  requestConnectorAdd: (request: ConnectorAddRequest) => void
  // Same idea for the main pipeline handle -- always creates and wires
  // another Agent node (see MainEdgeAddRequest's own comment).
  requestMainEdgeAdd: (request: MainEdgeAddRequest) => void
  // Same idea again, requested from an existing edge's own hover toolbar
  // (see EdgeInsertRequest's own comment).
  requestEdgeInsert: (request: EdgeInsertRequest) => void
  // The canvas's per-node Play icon (NodeHoverToolbar) -- runs one Agent
  // node in isolation via POST /protocols/{id}/nodes/{nodeId}/run. Only
  // ever called for a node with no upstream input (see AgentNode.tsx's own
  // canRunAlone computation); the backend re-validates this regardless.
  requestRunNode: (nodeId: string) => void
  // The canvas's per-node "Make experimental factor" icon (NodeHoverToolbar,
  // last button, every node type) -- opens the same field-picker dialog as
  // DesignTab's "Add factor," pre-filtered to this one node's own bindable
  // fields. A no-op when the protocol has no linked experiment yet (nothing
  // to attach a factor to), same as FactorBindableField's own disabled
  // state for that case.
  requestMakeFactor: (nodeId: string) => void
}

// Node renderers (AgentNode, CriticGateNode, ...) are deeply nested,
// self-contained components that otherwise only reach the canvas via
// useReactFlow() for their own node/edge mutations -- this context is the
// same idea, scoped to canvas-level UI state (which panel is open, which
// node is selected) that a plain node/edge mutation can't express.
const ProtocolCanvasContext = createContext<ProtocolCanvasActions | null>(null)

export function useProtocolCanvasActions(): ProtocolCanvasActions {
  const ctx = useContext(ProtocolCanvasContext)
  if (!ctx) throw new Error('useProtocolCanvasActions must be used within a ProtocolCanvas')
  return ctx
}

export const ProtocolCanvasActionsProvider = ProtocolCanvasContext.Provider
