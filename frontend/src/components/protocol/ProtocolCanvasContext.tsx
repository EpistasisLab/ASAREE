import { createContext, useContext } from 'react'

export type ConnectorSlot = 'llm' | 'tool' | 'memory' | 'architectural_pattern'

export interface ConnectorAddRequest {
  nodeId: string
  slot: ConnectorSlot
}

interface ProtocolCanvasActions {
  // Opens the "+" side panel filtered to whatever node type(s) fill this
  // slot (today always exactly one per slot, but the panel-based flow
  // still matches n8n's own picker rather than instant-creating, so this
  // generalizes for free once a slot ever offers more than one kind of
  // node). Selecting one creates it near the requesting node, wires the
  // edge into the right handle, and opens its Inspector immediately.
  requestConnectorAdd: (request: ConnectorAddRequest) => void
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
