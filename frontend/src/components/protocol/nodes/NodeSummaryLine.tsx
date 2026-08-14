import { TriangleAlert } from 'lucide-react'

// Every compact node card's second line: a truncated one-line summary of its
// own config -- or, when that config is incomplete, no line at all, and
// instead a small warning triangle pinned to the card's bottom-right corner
// (its parent node card must be `relative`, same as the run-status Badge
// pinned to the top-right corner) rather than prose like "Not configured"/
// "No goal set" taking up the line. Reuses --chart-4 (amber), the same
// "needs attention" hue cellsStatusAccent already uses elsewhere in this app.
export function NodeSummaryLine({ text, emptyLabel }: { text: string | null; emptyLabel: string }) {
  if (!text) {
    return (
      <div className="absolute right-1.5 bottom-1" title={emptyLabel}>
        <TriangleAlert className="size-3 shrink-0 text-[color:var(--chart-4)]" />
      </div>
    )
  }
  return (
    <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={text}>
      {text}
    </p>
  )
}
