import type { ProtocolEdge, ProtocolNode } from '@/types/protocols'
import { isMainEdge } from '@/lib/coordinationStrategy'

/** The reference syntax, on the authoring side.
 *
 * Prompts *store* `{{node:<id>}}` -- ids, so renaming a node can never break a
 * prompt that points at it. Prompts *display* `{{Analyst}}` -- labels, so the
 * sentence an experimenter is writing reads like a sentence. This module is the
 * translation between those two, plus the rule deciding which nodes may appear
 * on either side of it.
 *
 * The storage form is the authority: it is what
 * `services/prompt_references.py` parses and what the run actually resolves.
 * Nothing here changes what a prompt means -- it only changes how it reads
 * while being typed.
 */

/** Mirrors `prompt_references._BARE_TOKENS`. Not translated in either
 *  direction: they are already words rather than ids. */
const BARE_TOKENS = new Set(['previous', 'audience', 'upstream_instructions'])

export const PREVIOUS_TOKEN = '{{previous}}'

/** The storage form. Mirrors `prompt_references._REFERENCE_RE`'s node arm --
 *  deliberately narrow, because anything it does not match must survive
 *  untouched (a prompt may legitimately ask an agent to emit a template). */
const STORED_RE = /\{\{\s*node:([A-Za-z0-9_-]+)\s*(\|\s*raw\s*)?\}\}/gi

/** The display form: any `{{...}}` that is not obviously the storage form.
 *  Labels contain spaces and punctuation, so this is broad on purpose and the
 *  lookup below is what decides whether a match means anything. */
const DISPLAY_RE = /\{\{([^{}|]+?)(\|\s*raw\s*)?\}\}/g

export interface ReferenceTarget {
  id: string
  /** What the picker shows and what the prompt displays. Unique across the
   *  canvas -- see `displayNames`. */
  name: string
}

export interface PromptReferenceScope {
  /** What this node may reference, in canvas declaration order. The picker
   *  offers exactly this, because `validate_prompt_references` accepts exactly
   *  this -- an authoring surface that offered more would be offering something
   *  publish will later refuse. */
  targets: ReferenceTarget[]
  /** Display name for *every* node, not just the in-scope ones. A stored
   *  reference that has fallen out of scope (the user rewired) still has to
   *  render as a name the user recognizes, or the error naming it is
   *  unreadable. */
  names: Record<string, string>
}

export const EMPTY_SCOPE: PromptReferenceScope = { targets: [], names: {} }

/** Whether a bindable field's value ends up being the text references are
 *  resolved in. Mirrors `protocol_execution._node_seed_prompt`, which reads
 *  `config.prompt` and falls back to `config.goal` -- a reference anywhere else
 *  (a system prompt, a description) is literal text, so offering a picker there
 *  would promise a substitution that never happens. */
export function isPromptReferenceField(fieldPath: string): boolean {
  return fieldPath === 'config.prompt' || fieldPath === 'config.goal'
}

function baseName(node: ProtocolNode): string {
  const label = (node.data as { label?: string } | undefined)?.label
  return label?.trim() || (node.type === 'agent' ? 'Agent' : node.type)
}

/** A unique, human display name per node id.
 *
 * Two nodes sharing a label, or a node labelled "previous", would make
 * `{{X}}` ambiguous -- and an ambiguous display form cannot round-trip back to
 * one id. Both collisions are resolved by appending the id, which is ugly and
 * meant to be: it is the visible cost of duplicate labels, and renaming one of
 * them removes it.
 */
function displayNames(nodes: ProtocolNode[]): Record<string, string> {
  const counts = new Map<string, number>()
  for (const node of nodes) {
    const base = baseName(node).toLowerCase()
    counts.set(base, (counts.get(base) ?? 0) + 1)
  }
  const names: Record<string, string> = {}
  for (const node of nodes) {
    const base = baseName(node)
    const ambiguous = (counts.get(base.toLowerCase()) ?? 0) > 1 || BARE_TOKENS.has(base.toLowerCase())
    names[node.id] = ambiguous ? `${base} (${node.id})` : base
  }
  return names
}

/** Which nodes *nodeId*'s prompt may reference.
 *
 * Mirrors `protocol_execution.referenceable_node_ids`; the backend stays the
 * authority and re-checks at publish/plan/run. Computed here rather than
 * fetched because the canvas is client-side and this has to answer on every
 * rewire, while the prompt is being typed.
 *
 * Transitive main-edge ancestry, which is what "runs before this" actually
 * means. Not walk position: two parallel branches have an arbitrary relative
 * order, so a reference across them would resolve or not depending on
 * scheduling. Following main edges also excludes connector nodes (LLM, Dataset,
 * Memory, Tool), whose output is an inert placeholder rather than something
 * worth referencing.
 */
export function promptReferenceScope(
  nodes: ProtocolNode[],
  edges: ProtocolEdge[],
  nodeId: string | null,
): PromptReferenceScope {
  const names = displayNames(nodes)
  if (!nodeId) return { targets: [], names }

  const upstream = new Map<string, string[]>()
  for (const edge of edges) {
    if (!isMainEdge(edge)) continue
    upstream.set(edge.target, [...(upstream.get(edge.target) ?? []), edge.source])
  }

  const ancestors = new Set<string>()
  const frontier = [...(upstream.get(nodeId) ?? [])]
  while (frontier.length) {
    const current = frontier.pop()!
    // `current === nodeId` guards the cyclic canvases a conversation strategy
    // runs: a node is never its own ancestor, because it has no output yet at
    // the moment its own prompt is assembled.
    if (ancestors.has(current) || current === nodeId) continue
    ancestors.add(current)
    frontier.push(...(upstream.get(current) ?? []))
  }

  return {
    targets: nodes.filter((n) => ancestors.has(n.id)).map((n) => ({ id: n.id, name: names[n.id] })),
    names,
  }
}

/** Storage form -> display form. Ids become labels; everything else is left
 *  exactly as written, including a `{{node:...}}` whose node has been deleted
 *  (shown raw, because there is no name left to show and hiding it would hide
 *  the problem). */
export function toDisplayPrompt(stored: string, names: Record<string, string>): string {
  return (stored ?? '').replace(STORED_RE, (whole, id: string, raw?: string) =>
    names[id] ? `{{${names[id]}${raw ? '|raw' : ''}}}` : whole,
  )
}

/** Display form -> storage form. The inverse, and the one that runs on save.
 *
 * A `{{X}}` matching no node and no bare token is left verbatim rather than
 * dropped or guessed at: it is literal prompt text as far as the run is
 * concerned, and `unresolvedReferences` is what tells the user so.
 */
export function toStoredPrompt(display: string, names: Record<string, string>): string {
  const idsByName = new Map(Object.entries(names).map(([id, name]) => [name.toLowerCase(), id]))
  return (display ?? '').replace(DISPLAY_RE, (whole, target: string, raw?: string) => {
    const trimmed = target.trim()
    if (BARE_TOKENS.has(trimmed.toLowerCase())) return whole
    const id = idsByName.get(trimmed.toLowerCase())
    return id ? `{{node:${id}${raw ? '|raw' : ''}}}` : whole
  })
}

/** The `{{...}}` in a *display* prompt that name nothing.
 *
 * The hazard this display form carries: editing inside the braces (`{{Analyst}}`
 * -> `{{Analyzer}}`) turns a live reference into inert prose, and nothing about
 * the text says so. Surfacing them is what keeps that from being silent -- the
 * same reason a reference resolving empty is annotated rather than swallowed.
 */
export function unresolvedReferences(display: string, names: Record<string, string>): string[] {
  const known = new Set(Object.values(names).map((name) => name.toLowerCase()))
  const found: string[] = []
  for (const match of (display ?? '').matchAll(DISPLAY_RE)) {
    const target = match[1].trim()
    const lowered = target.toLowerCase()
    if (BARE_TOKENS.has(lowered) || known.has(lowered)) continue
    if (!found.includes(target)) found.push(target)
  }
  return found
}

/** Nodes referenced by a display prompt that exist but are not in scope.
 *
 * Distinct from `unresolvedReferences`: this one is a real node the user picked
 * or typed correctly, wired the wrong way round. The backend refuses it at
 * publish with the same reasoning, so saying it here just moves the discovery
 * to where it can be fixed.
 */
export function outOfScopeReferences(
  display: string,
  scope: PromptReferenceScope,
): string[] {
  const inScope = new Set(scope.targets.map((t) => t.name.toLowerCase()))
  const known = new Map(Object.values(scope.names).map((name) => [name.toLowerCase(), name]))
  const found: string[] = []
  for (const match of (display ?? '').matchAll(DISPLAY_RE)) {
    const lowered = match[1].trim().toLowerCase()
    if (BARE_TOKENS.has(lowered) || inScope.has(lowered)) continue
    const name = known.get(lowered)
    if (name && !found.includes(name)) found.push(name)
  }
  return found
}

/** Insert *text* into *value* at [start, end), returning the new value and
 *  where the caret should land. Kept here so the dropdown and the `{{`
 *  autocomplete cannot drift on the fiddly part. */
export function insertAt(value: string, start: number, end: number, text: string): { value: string; caret: number } {
  return { value: value.slice(0, start) + text + value.slice(end), caret: start + text.length }
}

/** The `{{` the caret is currently inside, if any, plus what has been typed
 *  after it -- what drives the inline autocomplete. Returns null the moment the
 *  fragment stops looking like a reference in progress (a closing brace, a
 *  newline), so the list dismisses itself rather than lingering. */
export function activeTrigger(value: string, caret: number): { start: number; query: string } | null {
  const start = value.lastIndexOf('{{', caret)
  if (start < 0) return null
  const fragment = value.slice(start + 2, caret)
  if (/[{}\n]/.test(fragment)) return null
  return { start, query: fragment.trim() }
}
