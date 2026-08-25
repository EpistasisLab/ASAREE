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

// One column per step of the main flow, one row per parallel branch. A host's
// own children reach from ~24px left of its card to ~260px right of it (the
// Knowledge slot at 90% of a w-72 card, plus a caption), and up to three
// ranks above it (see TIDY_CHILD_RANK_HEIGHT), so these are those bands plus
// a gap -- wide/tall enough that two adjacent hosts' children can't
// interleave, which is the thing that makes a hand-arranged canvas
// unreadable in the first place.
const TIDY_COLUMN_WIDTH = 460
const TIDY_ROW_HEIGHT = 520
// Slots on the agent's TOP edge -- their nodes sit above the host, everything
// else below. Mirrors addNode's own TOP_EDGE_SLOTS.
const TIDY_TOP_SLOTS = new Set<ConnectorSlot>(['architectural_pattern', 'skill', 'dataset', 'knowledge'])
const TIDY_CHILD_OFFSET_Y = 160
// When a child's own x is already taken, it moves one rank FURTHER from the
// host instead of sideways. Sideways is what findFreePosition does when a
// node is dropped one at a time (nothing better is knowable then), but a
// whole-canvas tidy knows the whole row up front -- and the top edge's four
// connectors are as little as 37px apart, so a sideways nudge there walks a
// node out from under its own connector and in under its neighbour's. Rank
// separation keeps every x exact and lets the edges do the explaining.
const TIDY_CHILD_RANK_HEIGHT = 120
const TIDY_MAX_CHILD_RANKS = 6

type TidyNode = { id: string; type?: string; position: XYPosition }
type TidyEdge = { source: string; target: string; targetHandle?: string | null }

/** Re-positions every node into a tidy left-to-right layout: hosts (agents and
 * critic gates) in main-flow order, each one's connector nodes directly
 * above/below the connector they're wired into, and anything unwired parked in
 * a row underneath. Returns the new position per node id; nodes are never
 * added, removed or re-typed, so this is always undoable by hand.
 *
 * Deliberately hand-rolled rather than dagre/elk: this graph isn't a general
 * DAG needing a general solver. It's a shallow chain of hosts whose satellites
 * have exactly one correct x (their own connector's, which connectorNodeOffsetX
 * already knows) and one of two correct y's. A layout library would have to be
 * fought to honor that and would price in a dependency for the privilege. */
export function tidyLayout(nodes: TidyNode[], edges: TidyEdge[]): Map<string, XYPosition> {
  const isHost = (n: TidyNode) => n.type === 'agent' || n.type === 'critic_gate'
  const hosts = nodes.filter(isHost)
  // A connector edge carries a targetHandle (the slot it feeds); a main-flow
  // edge between two hosts doesn't. So the presence of a handle is what
  // separates "this node configures that one" from "this node runs after
  // that one" -- see addNode's two branches.
  const childEdges = edges.filter((e) => !!e.targetHandle)
  const mainEdges = edges.filter((e) => !e.targetHandle)
  const childrenByHost = new Map<string, { id: string; slot: ConnectorSlot }[]>()
  const claimed = new Set<string>()
  for (const e of childEdges) {
    if (!childrenByHost.has(e.target)) childrenByHost.set(e.target, [])
    childrenByHost.get(e.target)!.push({ id: e.source, slot: e.targetHandle as ConnectorSlot })
    claimed.add(e.source)
  }

  // Longest-path layering over the main flow (Kahn's algorithm), so a host
  // always sits to the right of everything feeding it. Fan-in/fan-out are
  // both unrestricted here and nothing forbids a cycle, so whatever the
  // queue never reaches gets stacked after the last real column rather than
  // looping forever.
  const hostIds = new Set(hosts.map((h) => h.id))
  const column = new Map<string, number>()
  const indegree = new Map(hosts.map((h) => [h.id, 0]))
  const outgoing = new Map<string, string[]>()
  for (const e of mainEdges) {
    if (!hostIds.has(e.source) || !hostIds.has(e.target)) continue
    indegree.set(e.target, (indegree.get(e.target) ?? 0) + 1)
    if (!outgoing.has(e.source)) outgoing.set(e.source, [])
    outgoing.get(e.source)!.push(e.target)
  }
  const queue = hosts.filter((h) => (indegree.get(h.id) ?? 0) === 0).map((h) => h.id)
  for (const id of queue) column.set(id, 0)
  for (let head = 0; head < queue.length; head++) {
    const id = queue[head]
    for (const next of outgoing.get(id) ?? []) {
      column.set(next, Math.max(column.get(next) ?? 0, (column.get(id) ?? 0) + 1))
      indegree.set(next, (indegree.get(next) ?? 1) - 1)
      if ((indegree.get(next) ?? 0) === 0) queue.push(next)
    }
  }
  const lastColumn = Math.max(-1, ...[...column.values()])
  for (const h of hosts) if (!column.has(h.id)) column.set(h.id, lastColumn + 1)

  // Rows within a column keep the user's own top-to-bottom order: which
  // branch is "the first one" is a judgement this can't make, and silently
  // reshuffling parallel branches would lose real information.
  const positions = new Map<string, XYPosition>()
  const placed: XYPosition[] = []
  const byColumn = new Map<number, TidyNode[]>()
  for (const h of hosts) {
    const col = column.get(h.id)!
    if (!byColumn.has(col)) byColumn.set(col, [])
    byColumn.get(col)!.push(h)
  }
  for (const [col, group] of byColumn) {
    group.sort((a, b) => a.position.y - b.position.y)
    group.forEach((h, row) => {
      const position = { x: col * TIDY_COLUMN_WIDTH, y: row * TIDY_ROW_HEIGHT }
      positions.set(h.id, position)
      placed.push(position)
    })
  }

  const overlapsPlaced = (p: XYPosition) =>
    placed.some(
      (q) =>
        Math.abs(q.x - p.x) < CONNECTOR_CHILD_CLEARANCE.width && Math.abs(q.y - p.y) < CONNECTOR_CHILD_CLEARANCE.height,
    )

  // Children after every host, left column first, so the leftward host wins
  // any contested space and its neighbour's satellites are the ones nudged.
  // Within a host, the slots are placed in the order the edges were created,
  // which is the order the user wired them.
  const orderedHosts = [...hosts].sort(
    (a, b) => column.get(a.id)! - column.get(b.id)! || positions.get(a.id)!.y - positions.get(b.id)!.y,
  )
  for (const host of orderedHosts) {
    const hostPosition = positions.get(host.id)!
    for (const child of childrenByHost.get(host.id) ?? []) {
      const direction = TIDY_TOP_SLOTS.has(child.slot) ? -1 : 1
      const x = hostPosition.x + connectorNodeOffsetX(host.type, child.slot)
      let position: XYPosition | undefined
      for (let rank = 0; rank < TIDY_MAX_CHILD_RANKS; rank++) {
        const candidate = { x, y: hostPosition.y + direction * (TIDY_CHILD_OFFSET_Y + rank * TIDY_CHILD_RANK_HEIGHT) }
        if (!overlapsPlaced(candidate)) {
          position = candidate
          break
        }
      }
      // Six ranks deep means one connector is feeding a whole stack of nodes;
      // at that point exact-x has stopped paying for itself and the generic
      // search is the better answer.
      const fallback = { x, y: hostPosition.y + direction * TIDY_CHILD_OFFSET_Y }
      position ??= findFreePosition(placed, fallback, CONNECTOR_CHILD_CLEARANCE)
      positions.set(child.id, position)
      placed.push(position)
    }
  }

  // Anything wired to nothing -- a node dropped from the toolbar and not yet
  // connected, or one whose host was deleted -- goes in a row under the whole
  // layout rather than being left where it was. Leaving it put is what makes
  // a "tidy" look like it half-ran.
  const orphans = nodes.filter((n) => !isHost(n) && !claimed.has(n.id))
  if (orphans.length > 0) {
    const rows = Math.max(1, ...[...byColumn.values()].map((g) => g.length))
    const orphanY = rows * TIDY_ROW_HEIGHT
    orphans.sort((a, b) => a.position.x - b.position.x)
    orphans.forEach((n, i) => {
      const desired = { x: i * TIDY_COLUMN_WIDTH * 0.3, y: orphanY }
      const position = findFreePosition(placed, desired, CONNECTOR_CHILD_CLEARANCE)
      positions.set(n.id, position)
      placed.push(position)
    })
  }

  return positions
}
