import type { XYPosition } from '@xyflow/react'

// Roughly matches AgentNode/McpToolNode's rendered footprint (w-36 = 144px)
// plus breathing room, so "not overlapping" reads as visually separated,
// not just not-literally-touching.
const NODE_WIDTH = 170
const NODE_HEIGHT = 90

/** Nudges *desired* away from every position in *existing* until it clears
 * all of their bounding boxes -- an expanding-ring search (12 angles per
 * ring, growing radius) rather than a fixed offset, so it keeps working
 * regardless of how many nodes already cluster near the drop point. */
export function findFreePosition(existing: XYPosition[], desired: XYPosition): XYPosition {
  const overlaps = (p: XYPosition) =>
    existing.some((n) => Math.abs(n.x - p.x) < NODE_WIDTH && Math.abs(n.y - p.y) < NODE_HEIGHT)

  if (!overlaps(desired)) return desired

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
