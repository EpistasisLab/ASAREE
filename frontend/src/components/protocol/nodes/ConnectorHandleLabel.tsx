// A small always-visible caption under/beside a connector handle -- a
// hover-only `title` tooltip isn't "clearly labeled," it's easy to miss
// entirely on a canvas full of dots. Positioned absolutely so it doesn't
// affect the node card's own layout/height.

// Drawn in --node-label (a fixed yellow) rather than muted-foreground or the
// host node's own --card-accent: these captions sit OUTSIDE the card, on the
// canvas grid/scanline backdrop, where a dim gray reads as chrome and gets
// lost -- and they're not incidental metadata, they're the labels for the
// connectors the whole graph is built from. Yellow rather than the card
// accent because it has to stand out FROM the node, not match it, and it's
// the one warm hue no node kind owns (see index.css's --node-label).
//
// The faint background chip is what makes them legible over the grid lines
// they overlap; px-0.5 rather than a roomier px-1 because the four top-edge
// captions on AgentNode are only ~30px apart (see its own comment on why
// those x-positions are forced), so horizontal padding here is the one
// dimension that can't grow without them colliding.
const LABEL_CLASSNAME =
  'absolute rounded bg-background/70 px-0.5 text-[0.6rem] font-semibold whitespace-nowrap text-[color:var(--node-label)]'

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
      <span className={`${LABEL_CLASSNAME} -right-9 -translate-y-1/2`} style={{ top: top ?? '50%' }}>
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
      <span className={`${LABEL_CLASSNAME} -top-4 -translate-x-1/2`} style={{ left }}>
        {children}
      </span>
    )
  }
  return (
    <span className={`${LABEL_CLASSNAME} -bottom-4 -translate-x-1/2`} style={{ left }}>
      {children}
    </span>
  )
}
