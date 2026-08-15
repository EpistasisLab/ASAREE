import { TriangleAlert } from 'lucide-react'

// Every compact node card's second line: a truncated one-line summary of its
// own config, or -- only when `warning` names a real reason this node can't
// run (e.g. no LLM connected) -- a small warning triangle pinned to the
// card's bottom-right corner instead (its parent node card must be
// `relative`, same as the run-status Badge pinned to the top-right corner).
// `warning` takes priority over `text`: a run-blocking problem is more
// urgent than a config preview. An unset/blank field that DOESN'T block a
// run (e.g. Goal, optional since agentic-core has never required it) is
// exactly what `text` being null/empty already handles -- rendering
// nothing, not a warning; a blank-but-fine field was previously
// (incorrectly) conflated with "can't run" here. Reuses --chart-4 (amber),
// the same "needs attention" hue cellsStatusAccent already uses elsewhere.
export function NodeSummaryLine({ text, warning }: { text: string | null; warning?: string | null }) {
  if (warning) {
    return (
      <div className="absolute right-1.5 bottom-1" title={warning}>
        <TriangleAlert className="size-3 shrink-0 text-[color:var(--chart-4)]" />
      </div>
    )
  }
  if (!text) return null
  return (
    <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={text}>
      {text}
    </p>
  )
}
