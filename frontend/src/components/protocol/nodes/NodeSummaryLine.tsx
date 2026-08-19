import { WarningBadge } from './WarningBadge'

// Every compact node card's second line: a truncated one-line summary of its
// own config, or -- only when `warning` names a real reason this node can't
// run (e.g. no LLM connected) -- a small warning triangle pinned to the
// card's bottom-right corner instead (its parent node card must be
// `relative`, same as the run-status Badge pinned to the top-right corner).
// `warning` takes priority over `text`: a run-blocking problem is more
// urgent than a config preview. An unset/blank field that DOESN'T block a
// run (e.g. Goal, optional since Motoro has never required it) is
// exactly what `text` being null/empty already handles -- rendering
// nothing, not a warning; a blank-but-fine field was previously
// (incorrectly) conflated with "can't run" here.
export function NodeSummaryLine({ text, warning }: { text: string | null; warning?: string | string[] | null }) {
  if (warning) {
    return <WarningBadge issues={warning} className="absolute right-1.5 bottom-1" />
  }
  if (!text) return null
  return (
    <p className="truncate font-mono text-[0.65rem] text-muted-foreground" title={text}>
      {text}
    </p>
  )
}
