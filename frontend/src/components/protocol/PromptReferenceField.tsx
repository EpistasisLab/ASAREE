import { useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from 'react'
import { AlertTriangle, Braces } from 'lucide-react'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'
import { cn } from '@/lib/utils'
import {
  activeTrigger,
  insertAt,
  outOfScopeReferences,
  toDisplayPrompt,
  toStoredPrompt,
  unresolvedReferences,
  type PromptReferenceScope,
} from '@/lib/promptReferences'

// A prompt textarea that knows what this node may reference.
//
// Two entry points into ONE list, because they answer different questions.
// The "Insert reference" button is discovery: it is the only thing on screen
// telling someone who has never seen this feature that it exists. Typing `{{`
// is speed: once you know, reaching for the mouse mid-sentence is exactly the
// friction that makes people hand-type a token and get it wrong. Same
// component, same scoped data, so they cannot offer different things.
//
// The list is anchored below the textarea rather than floating at the caret.
// Measuring a caret inside a <textarea> needs a mirrored, identically-styled
// hidden div, which is ~100 lines of the most fragile kind -- and a fixed strip
// is easier to hit with the keyboard anyway, which is where this is used from.

interface Suggestion {
  /** What goes into the prompt, display form. */
  token: string
  /** What the row reads as -- the token without its braces. */
  title: string
  detail: string
}

function suggestionsFor(scope: PromptReferenceScope): { data: Suggestion[]; prose: Suggestion[] } {
  const first = scope.targets.length === 1 ? scope.targets[0].name : null
  return {
    data: [
      {
        token: '{{previous}}',
        title: 'previous',
        // Resolved inline so the common linear case can be inserted without
        // first working out which node "previous" means.
        detail: first ? `Output of the step before this one — ${first}` : 'Output of the step before this one',
      },
      // Each node, then the fields its Output Parser declares. The whole-node
      // entry always comes first: prose is what every node has, and the fields
      // are the extra a parser buys. A node with no parser has no field rows at
      // all, which is the picker saying so without a word of explanation.
      ...scope.targets.flatMap((target) => [
        {
          token: `{{${target.name}}}`,
          title: target.name,
          detail: target.fields?.length ? 'Full answer, plus its extracted fields' : 'Output of this node',
        },
        ...(target.fields ?? []).map((field) => ({
          token: `{{${target.name}.${field}}}`,
          title: `${target.name}.${field}`,
          detail: 'One extracted value, on its own',
        })),
      ]),
    ],
    prose: [
      { token: '{{audience}}', title: 'audience', detail: 'Who receives this agent’s output, in a sentence' },
      {
        token: '{{upstream_instructions}}',
        title: 'upstream_instructions',
        detail: 'Tells the agent that referenced output is material, not orders',
      },
    ],
  }
}

export function PromptReferenceField({
  id,
  label,
  description,
  rows = 4,
  className,
  placeholder,
  value,
  scope,
  trigger,
  onChange,
}: {
  id: string
  /** Omitted where the field is already labelled by something above it -- a
   *  factor level, whose own name Input is its label. */
  label?: string
  description?: ReactNode
  rows?: number
  className?: string
  /** Shown when empty. The System prompt uses it to display the exact default
   *  it falls back to, which its own description then refers to. */
  placeholder?: string
  /** Storage form -- `{{node:<id>}}`, exactly what the run resolves. */
  value: string
  scope: PromptReferenceScope
  /** The factor-binding affordance, rendered beside the label. */
  trigger?: ReactNode
  /** Called with the storage form. */
  onChange: (next: string) => void
}) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  // The last storage string this field itself produced. The draft below is
  // local state, so it would go stale if `value` changed underneath us (an
  // undo, a factor write); comparing against this distinguishes "our own echo"
  // from "somebody else edited it".
  const lastCommitted = useRef(value)
  const [draft, setDraft] = useState(() => toDisplayPrompt(value, scope.names))
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  // Where a `{{` trigger began, so accepting a suggestion replaces the partial
  // token rather than nesting a second one inside it. Null when the list was
  // opened by the button, where there is nothing to replace.
  const [triggerStart, setTriggerStart] = useState<number | null>(null)
  const [highlight, setHighlight] = useState(0)
  const pendingCaret = useRef<number | null>(null)

  useEffect(() => {
    if (value !== lastCommitted.current) {
      lastCommitted.current = value
      setDraft(toDisplayPrompt(value, scope.names))
    }
  }, [value, scope.names])

  useLayoutEffect(() => {
    if (pendingCaret.current === null) return
    const caret = pendingCaret.current
    pendingCaret.current = null
    textareaRef.current?.focus()
    textareaRef.current?.setSelectionRange(caret, caret)
  }, [draft])

  const { data, prose } = useMemo(() => suggestionsFor(scope), [scope])
  const matches = useMemo(() => {
    const all = [...data, ...prose]
    if (!query) return all
    const lowered = query.toLowerCase()
    return all.filter((s) => s.title.toLowerCase().includes(lowered))
  }, [data, prose, query])

  const unresolved = useMemo(() => unresolvedReferences(draft, scope.names), [draft, scope.names])
  const outOfScope = useMemo(() => outOfScopeReferences(draft, scope), [draft, scope])

  function commit(next: string) {
    setDraft(next)
    const stored = toStoredPrompt(next, scope.names)
    lastCommitted.current = stored
    onChange(stored)
  }

  function handleChange(next: string, caret: number) {
    commit(next)
    const active = activeTrigger(next, caret)
    if (active) {
      setTriggerStart(active.start)
      setQuery(active.query)
      setHighlight(0)
      setOpen(true)
    } else if (triggerStart !== null) {
      // Only a trigger-opened list auto-dismisses; one the user opened
      // deliberately stays until they choose or press Escape.
      setOpen(false)
      setTriggerStart(null)
    }
  }

  function accept(suggestion: Suggestion) {
    const area = textareaRef.current
    const caret = area?.selectionEnd ?? draft.length
    const start = triggerStart ?? area?.selectionStart ?? draft.length
    const { value: next, caret: nextCaret } = insertAt(draft, start, caret, suggestion.token)
    pendingCaret.current = nextCaret
    commit(next)
    setOpen(false)
    setTriggerStart(null)
    setQuery('')
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (!open || !matches.length) return
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault()
      setHighlight((i) => (i + (event.key === 'ArrowDown' ? 1 : matches.length - 1)) % matches.length)
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault()
      accept(matches[highlight])
    } else if (event.key === 'Escape') {
      event.preventDefault()
      // Escape dismisses the innermost thing, not the whole inspector. The
      // dialog's own dismissal listens for keydown on `document` (base-ui's
      // useDismiss, bubble phase) and it also has an onKeyDown on the popup,
      // so both the native and the React path have to be cut here -- without
      // this, closing the suggestion list throws away every unsaved edit in
      // the node behind it. Only reached while the list is open (see the
      // guard above), so Escape still closes the inspector otherwise.
      event.stopPropagation()
      setOpen(false)
      setTriggerStart(null)
    }
  }

  function openFromButton() {
    setQuery('')
    setTriggerStart(null)
    setHighlight(0)
    setOpen((wasOpen) => !wasOpen)
    textareaRef.current?.focus()
  }

  return (
    <div className="w-full space-y-1.5">
      <div className="flex items-center justify-between gap-2">
        {label ? (
          <Label htmlFor={id} className="flex items-center gap-1.5">
            {label}
            {trigger}
          </Label>
        ) : (
          <span className="flex items-center gap-1.5">{trigger}</span>
        )}
        <button
          type="button"
          onClick={openFromButton}
          aria-expanded={open}
          className="flex items-center gap-1 rounded px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground transition-colors hover:text-[color:var(--primary)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <Braces className="size-3" />
          Insert reference
        </button>
      </div>

      <Textarea
        id={id}
        ref={textareaRef}
        rows={rows}
        className={className}
        placeholder={placeholder}
        value={draft}
        onChange={(e) => handleChange(e.target.value, e.target.selectionStart)}
        onKeyDown={handleKeyDown}
        onBlur={() => setOpen(false)}
      />

      {open && (
        <div
          role="listbox"
          aria-label="Insert a reference"
          className="rounded-md border bg-background/95 p-1 shadow-lg"
          // Keeps the textarea focused (and its selection intact) so the
          // insertion lands where the caret actually is.
          onMouseDown={(e) => e.preventDefault()}
        >
          <div className="flex items-center justify-between px-2 py-1 text-[10px] uppercase tracking-wide text-muted-foreground">
            <span>Insert</span>
            <span className="font-mono">↑↓ · ↵ · esc</span>
          </div>
          {matches.length === 0 ? (
            <p className="px-2 py-1.5 text-xs text-muted-foreground">Nothing matches “{query}”.</p>
          ) : (
            matches.map((suggestion, index) => (
              <button
                key={suggestion.token}
                type="button"
                role="option"
                aria-selected={index === highlight}
                onMouseEnter={() => setHighlight(index)}
                onClick={() => accept(suggestion)}
                className={cn(
                  'flex w-full items-baseline gap-2 rounded px-2 py-1 text-left',
                  index === highlight ? 'bg-primary/15' : 'hover:bg-muted/50',
                )}
              >
                <span className="font-mono text-xs text-[color:var(--primary)]">{suggestion.title}</span>
                <span className="min-w-0 truncate text-[11px] text-muted-foreground">{suggestion.detail}</span>
              </button>
            ))
          )}
          {scope.targets.length === 0 && (
            <p className="border-t px-2 py-1.5 text-[11px] text-muted-foreground">
              Nothing upstream yet. Connect another agent into this one’s input to reference its output.
            </p>
          )}
        </div>
      )}

      {(unresolved.length > 0 || outOfScope.length > 0) && (
        <div className="flex items-start gap-1.5 rounded border border-[color:var(--chart-4)]/40 bg-[color:var(--chart-4)]/5 px-2 py-1.5 text-[11px] text-muted-foreground">
          <AlertTriangle className="mt-px size-3 shrink-0 text-[color:var(--chart-4)]" />
          <span className="min-w-0">
            {unresolved.length > 0 && (
              <>
                {unresolved.map((name) => `{{${name}}}`).join(', ')} {unresolved.length === 1 ? 'names' : 'name'} no node
                on this canvas, so {unresolved.length === 1 ? 'it is' : 'they are'} sent to the agent as literal text.
              </>
            )}
            {unresolved.length > 0 && outOfScope.length > 0 && ' '}
            {outOfScope.length > 0 && (
              <>
                {outOfScope.join(', ')} {outOfScope.length === 1 ? 'does' : 'do'} not run before this agent, so publishing
                will be refused until {outOfScope.length === 1 ? 'it is' : 'they are'} wired upstream.
              </>
            )}
          </span>
        </div>
      )}

      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  )
}
