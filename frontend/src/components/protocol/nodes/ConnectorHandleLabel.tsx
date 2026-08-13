// A small always-visible caption under/beside a connector handle -- a
// hover-only `title` tooltip isn't "clearly labeled," it's easy to miss
// entirely on a canvas full of dots. Positioned absolutely so it doesn't
// affect the node card's own layout/height.
export function ConnectorHandleLabel({
  left,
  side = 'bottom',
  children,
}: {
  left?: string
  side?: 'bottom' | 'right'
  children: string
}) {
  if (side === 'right') {
    return (
      <span className="absolute top-1/2 -right-9 -translate-y-1/2 text-[0.55rem] font-medium whitespace-nowrap text-muted-foreground">
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
