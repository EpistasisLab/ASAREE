// A small always-visible caption under/beside a connector handle -- a
// hover-only `title` tooltip isn't "clearly labeled," it's easy to miss
// entirely on a canvas full of dots. Positioned absolutely so it doesn't
// affect the node card's own layout/height.
export function ConnectorHandleLabel({
  left,
  top,
  side = 'bottom',
  children,
}: {
  left?: string
  // Only meaningful for side="right" -- lets two right-side connectors (e.g.
  // McpToolNode's main output and its Tool connector) each get their own
  // vertical slot instead of both defaulting to dead center.
  top?: string
  side?: 'bottom' | 'right' | 'top'
  children: string
}) {
  if (side === 'right') {
    return (
      <span
        className="absolute -right-9 -translate-y-1/2 text-[0.55rem] font-medium whitespace-nowrap text-muted-foreground"
        style={{ top: top ?? '50%' }}
      >
        {children}
      </span>
    )
  }
  if (side === 'top') {
    // Beside the handle, not above it -- a top-edge connector on a node
    // positioned ABOVE it (see AgentNode.tsx's own Architectural Pattern
    // connector) sits close enough to its own source node that a label
    // floating further up would crowd into it; anchoring at the handle's own
    // `left` and nudging right with a fixed margin instead reads like a
    // side-mounted tag, same idea as side="right"'s own beside-the-node
    // placement.
    return (
      <span
        className="absolute top-0 ml-2 -translate-y-1/2 text-[0.55rem] font-medium whitespace-nowrap text-muted-foreground"
        style={{ left }}
      >
        {children}
      </span>
    )
  }
  return (
    <span
      className="absolute -bottom-4 -translate-x-1/2 text-[0.55rem] font-medium whitespace-nowrap text-muted-foreground"
      style={{ left }}
    >
      {children}
    </span>
  )
}
