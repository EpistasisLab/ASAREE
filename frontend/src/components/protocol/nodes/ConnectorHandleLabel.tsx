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
    // Centered directly ABOVE its handle -- an exact mirror of the bottom
    // branch below, so the three top-edge captions (Pattern / Skill /
    // Resource) read the same way as AI / Memory / Tool do underneath. They
    // used to hang off to one side of the handle instead, which meant a
    // caption's own position had to be reasoned about per-connector
    // (left-hanging near the right corner, right-hanging near the left one);
    // centering makes the caption belong unambiguously to the dot below it,
    // which is what a label on a connector is for.
    return (
      <span
        className="absolute -top-4 -translate-x-1/2 text-[0.55rem] font-medium whitespace-nowrap text-muted-foreground"
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
