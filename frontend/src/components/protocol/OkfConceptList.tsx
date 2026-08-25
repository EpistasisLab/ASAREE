import { useState } from 'react'
import { BadgeCheck } from 'lucide-react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

// The bundle preview used to render list_concepts' raw JSON in a <pre>, on the
// reasoning that the OKF tools' output shape is Motoro's to change and parsing
// it here would be a second place to keep in sync. That holds for the SHAPE,
// not for the reader: a researcher opening this inspector wants to know what
// knowledge the agent has, and a wall of escaped JSON answers that badly.
//
// So this parses defensively rather than trusting the contract: anything that
// isn't the expected {concepts: [...]} payload falls through to the raw text
// (`kind: 'raw'`), so a Motoro-side change degrades to the old rendering rather
// than to an empty panel or a crash. Every field is optional on the way in for
// the same reason -- a hand-authored concept can omit any of them, and one
// missing `title` must not cost you the rest of the list.

type Stamp = { by: string; at: string }

type Concept = {
  id: string
  type: string
  title: string
  tags: string[]
  status: string
  verified: Stamp[]
  generated: Stamp | null
}

type Parsed =
  | { kind: 'concepts'; concepts: Concept[] }
  // The server answered, but with a failure -- e.g. AGENTIC_OKF_BUNDLE_DIR
  // unset, or a tool that raised. Worth saying plainly; it is not "no concepts".
  | { kind: 'error'; message: string }
  | { kind: 'raw' }

function asText(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

// `generated`/`verified` are {by, at}, and `verified` accumulates into a LIST on
// a second confirmation -- so one entry and many arrive in different shapes for
// the same field (see Motoro's mark_verified).
function asStamps(value: unknown): Stamp[] {
  const entries = Array.isArray(value) ? value : [value]
  return entries.flatMap((entry) => {
    if (!entry || typeof entry !== 'object') return []
    const record = entry as Record<string, unknown>
    return [{ by: asText(record.by), at: asText(record.at) }]
  })
}

function parseConcepts(content: string): Parsed {
  let payload: unknown
  try {
    payload = JSON.parse(content)
  } catch {
    return { kind: 'raw' }
  }
  if (!payload || typeof payload !== 'object') return { kind: 'raw' }
  const record = payload as Record<string, unknown>
  if (typeof record.error === 'string') return { kind: 'error', message: record.error }
  if (!Array.isArray(record.concepts)) return { kind: 'raw' }
  const concepts = record.concepts.flatMap((raw): Concept[] => {
    if (!raw || typeof raw !== 'object') return []
    const fields = raw as Record<string, unknown>
    return [
      {
        id: asText(fields.id),
        type: asText(fields.type),
        title: asText(fields.title),
        tags: Array.isArray(fields.tags) ? fields.tags.map(asText).filter(Boolean) : [],
        status: asText(fields.status),
        verified: asStamps(fields.verified),
        generated: asStamps(fields.generated)[0] ?? null,
      },
    ]
  })
  return { kind: 'concepts', concepts }
}

// A YAML timestamp as a plain date, falling back to the raw string: the value
// comes out of user-authored frontmatter, so it isn't guaranteed to be a date at
// all, and rendering "Invalid Date" would be worse than showing what's there.
function formatStamp(at: string): string {
  if (!at) return ''
  const parsed = new Date(at)
  return Number.isNaN(parsed.getTime()) ? at : parsed.toLocaleDateString()
}

function stampTitle(label: string, stamp: Stamp): string {
  const at = formatStamp(stamp.at)
  return [label, stamp.by && `by ${stamp.by}`, at && `on ${at}`].filter(Boolean).join(' ')
}

// Grouped by concept type, the OKF spec's own primary axis (Metric, Data Table,
// Playbook...) and how a researcher scans a bundle: "what playbooks does it
// have", not "what is item 7". Types in first-seen order -- list_concepts walks
// the tree sorted, so that's directory order, the arrangement its author chose.
function groupByType(concepts: Concept[]): [string, Concept[]][] {
  const groups = new Map<string, Concept[]>()
  for (const concept of concepts) {
    const key = concept.type || 'Untyped'
    const existing = groups.get(key)
    if (existing) existing.push(concept)
    else groups.set(key, [concept])
  }
  return [...groups.entries()]
}

export function OkfConceptList({ content, isError = false }: { content: string; isError?: boolean }) {
  // The raw payload stays one click away rather than being deleted: it's what
  // the agent's own list_concepts call returns, so it's the thing to look at
  // when this rendering and the bundle seem to disagree.
  const [showRaw, setShowRaw] = useState(false)
  // isError means the tool raised, so `content` is an exception message rather
  // than a payload -- reported as an error instead of parsed as one.
  const parsed: Parsed = isError ? { kind: 'error', message: content } : parseConcepts(content)
  const count = parsed.kind === 'concepts' ? parsed.concepts.length : 0

  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          {parsed.kind === 'concepts'
            ? count === 0
              ? 'No concepts yet — this bundle stays empty until the agent writes to it'
              : `Knowledge in this bundle (${count})`
            : 'Concepts currently in this bundle'}
        </p>
        {parsed.kind === 'concepts' && count > 0 && (
          <Button
            variant="link"
            size="sm"
            className="h-auto p-0 text-[11px]"
            onClick={() => setShowRaw((previous) => !previous)}
          >
            {showRaw ? 'Show list' : 'Show raw'}
          </Button>
        )}
      </div>

      {parsed.kind === 'error' ? (
        <p className="text-xs text-destructive">This bundle&rsquo;s server reported: {parsed.message}</p>
      ) : parsed.kind === 'raw' || showRaw ? (
        <pre className="max-h-[calc(100vh-30rem)] min-h-24 overflow-auto rounded-md border bg-muted/30 p-2 font-mono text-[0.7rem] whitespace-pre-wrap">
          {content || '(empty)'}
        </pre>
      ) : count === 0 ? null : (
        <div className="max-h-[calc(100vh-30rem)] space-y-2 overflow-auto rounded-md border bg-muted/30 p-2">
          {groupByType(parsed.concepts).map(([type, group]) => (
            <div key={type} className="space-y-1">
              <p className="text-[10px] tracking-wider text-muted-foreground/70 uppercase">
                {type} ({group.length})
              </p>
              <ul className="space-y-1">
                {group.map((concept, index) => (
                  <li
                    key={concept.id || `${type}-${index}`}
                    className="rounded border border-border/60 bg-background/40 px-2 py-1.5"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="min-w-0 flex-1 truncate text-xs" title={concept.title || concept.id}>
                        {concept.title || <span className="text-muted-foreground">(untitled)</span>}
                      </p>
                      <div className="flex shrink-0 items-center gap-1">
                        {concept.status && (
                          <Badge variant="secondary" className="px-1 py-0 text-[10px]">
                            {concept.status}
                          </Badge>
                        )}
                        {/* The spec's own distinction: a concept can be
                            re-confirmed without being regenerated, and whether
                            anyone has confirmed this one is the first thing you
                            want to know about knowledge an agent will act on.
                            The title sits on a wrapping span, not the icon --
                            `title` is an HTML attribute, and on an <svg> it
                            isn't the SVG <title> element a browser shows. */}
                        {concept.verified.length > 0 && (
                          <span title={stampTitle('Verified', concept.verified[concept.verified.length - 1])}>
                            <BadgeCheck className="size-3.5 text-[color:var(--chart-3)]" />
                          </span>
                        )}
                      </div>
                    </div>
                    <p
                      className="truncate font-mono text-[10px] text-muted-foreground/70"
                      title={concept.generated ? stampTitle(`${concept.id} — written`, concept.generated) : concept.id}
                    >
                      {concept.id}
                    </p>
                    {concept.tags.length > 0 && (
                      <p
                        className="truncate font-mono text-[10px] text-muted-foreground/50"
                        title={concept.tags.join(', ')}
                      >
                        {concept.tags.join(' · ')}
                      </p>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
