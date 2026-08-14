import { createContext, useContext } from 'react'

export type ConnectorSlot = 'llm' | 'tool' | 'memory' | 'architectural_pattern'

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

interface ProtocolCanvasActions {
  // Opens the "+" side panel filtered to whatever node type(s) fill this
  // slot (today always exactly one per slot, but the panel-based flow
  // still matches n8n's own picker rather than instant-creating, so this
  // generalizes for free once a slot ever offers more than one kind of
  // node). Selecting one creates it near the requesting node, wires the
  // edge into the right handle, and opens its Inspector immediately.
  requestConnectorAdd: (request: ConnectorAddRequest) => void
  // Same idea for the main pipeline handle -- always creates and wires
  // another Agent node (see MainEdgeAddRequest's own comment).
  requestMainEdgeAdd: (request: MainEdgeAddRequest) => void
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
