import type { XYPosition } from '@xyflow/react'
import type { ConnectorSlot } from './ProtocolCanvasContext'

// Roughly matches AgentNode/McpToolNode's rendered footprint (w-36 = 144px)
// plus breathing room, so "not overlapping" reads as visually separated,
// not just not-literally-touching.
const NODE_WIDTH = 170
const NODE_HEIGHT = 90

// Where each connector sits along its host card's edge, as a fraction of that
// card's width. AgentNode renders its handles, captions and "+" stubs from
// this table (connectorLefts), and addNode() drops the node a connector asked
// for at that same x (connectorNodeOffsetX) -- one table, so a new node can't
// land under a different connector than the one that requested it just
// because the markup and the placement code drifted apart. See AgentNode's
// own comment for WHY these x-positions are what they are (the hover toolbar
// owns the middle of the card, so all four top-edge slots live in the margins).
const CONNECTOR_X: { agent: Record<ConnectorSlot, number>; critic_gate: Partial<Record<ConnectorSlot, number>> } = {
  agent: {
    architectural_pattern: 0.05,
    skill: 0.18,
    dataset: 0.71,
    knowledge: 0.9,
    ai: 0.2,
    memory: 0.5,
    tool: 0.8,
  },
  critic_gate: { ai: 0.5 },
}

// The host cards' own widths: AgentNode is w-72, CriticGateNode w-36.
const HOST_WIDTH: Record<string, number> = { agent: 288, critic_gate: 144 }

// A connector's node is a CircleNode: a 56px circle under a caption that can
// grow to 96px, with the circle -- where its handle is -- centered in
// whichever of the two is wider. Half of the middle of that range is close
// enough to center any of them on a connector; a few px off is invisible,
// and being a whole card-width off is the thing this fixes.
const NEW_NODE_HALF_WIDTH = 38

/** The `left` style for each of a host card's connectors, as a percentage
 * string — for the `<Handle>`, its caption and its "+" stub, which must all
 * sit at the same x. */
export function connectorLefts(host: 'agent' | 'critic_gate'): Record<ConnectorSlot, string> {
  const table = CONNECTOR_X[host]
  const slots = Object.keys(CONNECTOR_X.agent) as ConnectorSlot[]
  return Object.fromEntries(slots.map((slot) => [slot, `${(table[slot] ?? 0.5) * 100}%`])) as Record<ConnectorSlot, string>
}

/** How far right of a host node's own position to place the new node a
 * connector just asked for, so the node lands centered on that connector
 * rather than on the host's left corner. Unknown host type falls back to
 * the middle of an agent-sized card. */
export function connectorNodeOffsetX(hostType: string | undefined, slot: ConnectorSlot): number {
  const table = hostType === 'agent' || hostType === 'critic_gate' ? CONNECTOR_X[hostType] : undefined
  const width = (hostType && HOST_WIDTH[hostType]) ?? HOST_WIDTH.agent
  return (table?.[slot] ?? 0.5) * width - NEW_NODE_HALF_WIDTH
}

/** The clearance to use for the small CircleNode a connector's "+" creates,
 * instead of the agent-sized default. An agent's own connectors are as little
 * as 37px apart (Pattern/Skill), so measuring these against a 170px box would
 * declare a collision for two nodes that fit side by side comfortably and
 * shove the second one a full card-width right — under a different
 * connector, which is the thing anchoring them to their connector is for.
 * 84x80 is roughly a circle-plus-caption's real footprint, and 84 rather
 * than 90 on purpose: the bottom row's three connectors are 86px apart on a
 * w-72 card, so at 84 a full AI + Memory + Tool row keeps every node exactly
 * under its own connector with nothing displaced. */
export const CONNECTOR_CHILD_CLEARANCE = { width: 84, height: 80 }

/** Nudges *desired* away from every position in *existing* until it clears
 * all of their bounding boxes — first straight to the right, then via an
 * expanding-ring search (12 angles per ring, growing radius) so it keeps
 * working regardless of how many nodes already cluster near the drop point. */
export function findFreePosition(
  existing: XYPosition[],
  desired: XYPosition,
  clearance: { width: number; height: number } = { width: NODE_WIDTH, height: NODE_HEIGHT },
): XYPosition {
  const overlaps = (p: XYPosition) =>
    existing.some((n) => Math.abs(n.x - p.x) < clearance.width && Math.abs(n.y - p.y) < clearance.height)

  if (!overlaps(desired)) return desired

  // Rightward along the same row first. The desired spot is meaningful, not
  // arbitrary -- a connector's "+" wants its node directly under that
  // connector -- so when it's taken, the nearest free spot on the same row
  // keeps the node beside the connector that asked for it. The ring search
  // below would just as happily put it 100px above the host card, i.e.
  // hovering over some other connector's node.
  //
  // Scanned in thirds of a clearance rather than whole steps: a whole step
  // overshoots (it clears the node it collided with by a full width), and
  // with four connectors in a row those overshoots compound until the last
  // node is nowhere near its own connector.
  const scan = clearance.width / 3
  for (let step = 1; step <= 18; step++) {
    const candidate = { x: desired.x + step * scan, y: desired.y }
    if (!overlaps(candidate)) return candidate
  }

  const anglesPerRing = 12
  for (let ring = 1; ring <= 30; ring++) {
    const radius = ring * 60
    for (let i = 0; i < anglesPerRing; i++) {
      const angle = (i / anglesPerRing) * Math.PI * 2
      const candidate = { x: desired.x + Math.cos(angle) * radius, y: desired.y + Math.sin(angle) * radius }
      if (!overlaps(candidate)) return candidate
    }
  }
  return desired // give up after a generous search -- better to place it than never add the node
}
