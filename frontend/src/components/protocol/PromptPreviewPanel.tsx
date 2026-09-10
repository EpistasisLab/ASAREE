import { useEffect, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, RefreshCw } from 'lucide-react'
import { ApiError } from '@/api/client'
import type { PromptPreview } from '@/types/protocols'

// How many `<output of "...">` stand-ins the preview contains -- see the
// character-budget note in the panel. Matches what `preview_node_prompt`
// writes; it is the only thing in an assembled prompt with that shape.
const PLACEHOLDER_RE = /<output of "/g

const DEBOUNCE_MS = 500

// What this agent will actually be given, assembled by the backend and not
// re-derived here on purpose: a preview that drifts from the real prompt would
// be read as evidence about a run it does not describe. See
// `services/protocol_execution.preview_node_prompt`.
//
// Collapsed by default and fetched only once opened -- most inspector opens are
// there to edit a field, and the envelope only becomes interesting when you
// doubt what reaches the model.
export function PromptPreviewPanel({
  signature,
  fetchPreview,
}: {
  // Changes whenever something the preview depends on changes. The canvas
  // cannot be rewired while this modal is open, so the node's own data is the
  // whole dependency -- the caller serializes it rather than this component
  // guessing which fields matter.
  signature: string
  // Passed as a callback rather than (protocolId, nodeId, graph) because the
  // graph it has to send is the live canvas, which only the canvas holds. Read
  // through a ref below so re-creating it every keystroke doesn't refetch.
  fetchPreview: () => Promise<PromptPreview>
}) {
  const [open, setOpen] = useState(false)
  const [preview, setPreview] = useState<PromptPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [nonce, setNonce] = useState(0)
  const fetchRef = useRef(fetchPreview)
  fetchRef.current = fetchPreview

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    // Debounced, because `signature` changes on every keystroke in the prompt
    // field above and each change is a request.
    const timer = setTimeout(() => {
      fetchRef
        .current()
        .then((result) => {
          if (cancelled) return
          setPreview(result)
          setError(null)
        })
        .catch((err: unknown) => {
          if (cancelled) return
          setPreview(null)
          setError(err instanceof ApiError ? err.message : 'Could not assemble a preview.')
        })
        .finally(() => {
          if (!cancelled) setLoading(false)
        })
    }, DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(timer)
    }
  }, [open, signature, nonce])

  const placeholders = preview ? (preview.text.match(PLACEHOLDER_RE) ?? []).length : 0

  return (
    <div className="@container space-y-2 rounded-md border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <button
          type="button"
          onClick={() => setOpen((value) => !value)}
          aria-expanded={open}
          className="flex cursor-pointer items-center gap-1.5 text-sm font-medium hover:text-primary"
        >
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          {/* Future tense, pairing with `ReceivedPromptPanel`'s past tense
              directly below it in the Input pane: the same question asked of a
              design and of a run that already happened. */}
          Prompt this agent will receive
        </button>
        {open && preview && (
          <>
            <span className="rounded-sm bg-primary/10 px-1.5 py-px font-mono text-[0.65rem] text-primary" title="Which prompt contract assembled this. Stamped on the experiment at creation and never moved.">
              contract v{preview.contract_version}
            </span>
            <button
              type="button"
              onClick={() => setNonce((n) => n + 1)}
              className="ml-auto flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground"
            >
              <RefreshCw className={`size-3 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </button>
          </>
        )}
      </div>

      {!open ? (
        <p className="text-xs text-muted-foreground">
          The exact user message, assembled the way a run assembles it — your prompt with every reference resolved,
          plus whatever the incoming edges deliver, and the dataset and script cues.
        </p>
      ) : loading && !preview ? (
        <p className="text-xs text-muted-foreground">Assembling…</p>
      ) : error ? (
        <p className="rounded border border-destructive/30 bg-destructive/5 px-2 py-1.5 text-xs text-destructive">{error}</p>
      ) : preview ? (
        <>
          {/* Rendered as text, never markdown: on the current contract a
              reference resolves to whatever an upstream model emitted, and at
              design time to a placeholder that looks like an HTML tag. */}
          <pre className="max-h-64 overflow-auto rounded border bg-background/70 p-2 font-mono text-[11px] whitespace-pre-wrap break-words @md:max-h-96">
            {preview.text}
          </pre>
          <p className="text-xs text-muted-foreground">
            {preview.text.length.toLocaleString()} characters
            {placeholders > 0 && (
              // Placeholder honesty: a stand-in is a few dozen characters where
              // a real answer can be thousands, so a preview read as a size
              // estimate would be badly wrong. Say so next to the number.
              <>
                , counting {placeholders === 1 ? 'one stand-in' : `${placeholders} stand-ins`} for output that does not
                exist until the run. The real prompt will be longer by however much each sender writes.
              </>
            )}
          </p>
        </>
      ) : null}
    </div>
  )
}
