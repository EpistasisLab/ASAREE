import type { OutputContract, OutputParserNodeConfig, ProtocolEdge, ProtocolNode } from '@/types/protocols'
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
 *  direction: it is already a word rather than an id.
 *
 *  A set of one, kept as a set because the checks below read as "is this a
 *  bare token" and because that is the right shape for the concept even at
 *  n=1. It used to hold `audience` and `upstream_instructions` as well; both
 *  resolved to platform-composed sentences rather than to data, and are
 *  withdrawn. */
const BARE_TOKENS = new Set(['previous'])

export const PREVIOUS_TOKEN = '{{previous}}'

/** `{{previous}}` in a stored prompt. Not global: every caller only asks
 *  whether it is there at all, and a `lastIndex` carried between calls on a
 *  shared global regex is a bug waiting to happen. */
const PREVIOUS_RE = /\{\{\s*previous\s*(\|\s*raw\s*)?\}\}/i

/** The storage form. Mirrors `prompt_references._REFERENCE_RE`'s node arm --
 *  deliberately narrow, because anything it does not match must survive
 *  untouched (a prompt may legitimately ask an agent to emit a template). */
const STORED_RE = /\{\{\s*node:([A-Za-z0-9_-]+)(?:\.([A-Za-z_][A-Za-z0-9_]*))?\s*(\|\s*raw\s*)?\}\}/gi

/** An Output Parser field name, on its own. Mirrors `prompt_references._FIELD`:
 *  it becomes an attribute on the model Motoro builds from the contract, so it
 *  has to be an identifier -- which is also what makes the display form's
 *  `Name.field` split unambiguous enough to reverse. */
const FIELD_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

/** The display form: any `{{...}}` that is not obviously the storage form.
 *  Labels contain spaces and punctuation, so this is broad on purpose and the
 *  lookup below is what decides whether a match means anything. */
const DISPLAY_RE = /\{\{([^{}|]+?)(\|\s*raw\s*)?\}\}/g

export interface ReferenceTarget {
  id: string
  /** What the picker shows and what the prompt displays. Unique across the
   *  canvas -- see `displayNames`. */
  name: string
  /** The fields this node's Output Parser declares, if one is wired to it.
   *
   *  Field references hang off the parser, not off the agent: an agent with no
   *  parser has no typed output to name, so the picker offers it only as a
   *  whole. Empty is the normal case. */
  fields?: string[]
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

/** Split a display target into its node name and its optional field.
 *
 * A full-name match wins outright, so a node literally labelled `Analyst.x`
 * still round-trips. Otherwise the split is at the LAST dot, and the tail has
 * to be a bare identifier with no surrounding space -- which is what keeps a
 * label like `Step 1. Profiler` from being read as a field reference.
 */
function splitDisplayTarget(target: string, isName: (candidate: string) => boolean): { name: string; field: string } {
  const trimmed = target.trim()
  if (isName(trimmed)) return { name: trimmed, field: '' }
  const dot = trimmed.lastIndexOf('.')
  if (dot <= 0) return { name: trimmed, field: '' }
  const field = trimmed.slice(dot + 1)
  if (!FIELD_RE.test(field)) return { name: trimmed, field: '' }
  return { name: trimmed.slice(0, dot), field }
}

/** The field names a node's Output Parser declares.
 *
 * Mirrors `protocol_execution._resolve_output_contract`, including its
 * precedence: a wired parser wins, a disabled one declares nothing (its
 * extraction is suspended, so nothing will be there to read), and only a node
 * with no parser wired at all falls back to the legacy `config.output_contract`
 * field it may still be carrying.
 */
function parserFields(nodes: ProtocolNode[], edges: ProtocolEdge[], nodeId: string): string[] {
  const byId = new Map(nodes.map((n) => [n.id, n]))
  let wired = false
  for (const edge of edges) {
    if (edge.target !== nodeId || edge.targetHandle !== 'output_parser') continue
    const source = byId.get(edge.source)
    if (source?.type !== 'output_parser') continue
    wired = true
    const config = (source.data as { config?: OutputParserNodeConfig } | undefined)?.config
    if (config?.enabled === false) continue
    const names = contractFieldNames(config?.output_contract)
    if (names.length) return names
  }
  if (wired) return []
  const legacy = (byId.get(nodeId)?.data as { config?: { output_contract?: OutputContract | null } } | undefined)
    ?.config?.output_contract
  return contractFieldNames(legacy)
}

function contractFieldNames(contract: OutputContract | null | undefined): string[] {
  return (contract?.fields ?? []).map((field) => field.name.trim()).filter(Boolean)
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
    targets: nodes
      .filter((n) => ancestors.has(n.id))
      .map((n) => {
        const fields = parserFields(nodes, edges, n.id)
        return { id: n.id, name: names[n.id], ...(fields.length ? { fields } : {}) }
      }),
    names,
  }
}

/** A direct main-edge neighbour.
 *
 *  Inherits `fields` from `ReferenceTarget` -- the shape the peer's own Output
 *  Parser declares. It is carried here because the shape a sender promises is
 *  only useful to whoever reads that sender, and the consuming *agent* is never
 *  told it: the prompt has no envelope to put it in, and inventing
 *  one would reintroduce exactly the unasked-for platform prose that design
 *  removed. So it is surfaced to the user instead, in the Receives readout,
 *  where a mismatch is something they can act on while wiring. */
export type HandoffPeer = ReferenceTarget

export interface HandoffPeers {
  /** Direct main-edge predecessors -- the nodes whose output is handed to this
   *  one, and exactly what `{{previous}}` expands to. */
  receives: HandoffPeer[]
  /** Direct main-edge successors. Empty means this node's answer is the run's. */
  sends: HandoffPeer[]
}

/** Who hands off to *nodeId*, and who it hands off to.
 *
 * One edge each way, not the transitive ancestry `promptReferenceScope`
 * returns: this answers "what is wired to me", which is the thing a user can
 * check against the canvas in front of them. Reach-back is a separate,
 * deliberate act and shows up in the picker instead.
 *
 * Lives here rather than in a graph module because the `receives` list *is*
 * `{{previous}}`'s expansion (mirroring `_upstream_ids`, which is what the
 * backend resolves that token against), and because it shares this module's
 * display naming -- the same node has to read as the same name in the picker,
 * in the prompt, and in this readout.
 */
export function handoffPeers(nodes: ProtocolNode[], edges: ProtocolEdge[], nodeId: string | null): HandoffPeers {
  const names = displayNames(nodes)
  if (!nodeId) return { receives: [], sends: [] }
  const known = new Set(nodes.map((n) => n.id))
  const peers = (pick: (edge: ProtocolEdge) => string, match: (edge: ProtocolEdge) => string) => {
    const ids: string[] = []
    for (const edge of edges) {
      if (!isMainEdge(edge) || match(edge) !== nodeId) continue
      const id = pick(edge)
      if (id !== nodeId && known.has(id) && !ids.includes(id)) ids.push(id)
    }
    // Canvas declaration order, matching the picker -- an edge list's own order
    // is whatever the user happened to draw in.
    return nodes
      .filter((n) => ids.includes(n.id))
      .map((n) => {
        const fields = parserFields(nodes, edges, n.id)
        return { id: n.id, name: names[n.id], ...(fields.length ? { fields } : {}) }
      })
  }
  return {
    receives: peers((e) => e.source, (e) => e.target),
    sends: peers((e) => e.target, (e) => e.source),
  }
}

/** The text a node's references are actually resolved in.
 *
 * Mirrors `_node_seed_prompt`: `prompt`, falling back to `goal`, falling back
 * to the label -- so a reference written in Goal on a node with no Prompt is
 * live, and the same reference becomes inert the moment a Prompt is typed.
 * Anywhere that asks "does this node reference X" has to ask it of this string
 * and not of `config.prompt` alone.
 */
export function seedPromptText(node: ProtocolNode): string {
  const data = (node.data ?? {}) as { label?: string; config?: { prompt?: string | null; goal?: string | null } }
  return data.config?.prompt || data.config?.goal || data.label || ''
}

/** Whether a stored prompt uses `{{previous}}`.
 *
 * Its own answer, unlike every other reference, is not in the text: it means
 * whatever the wiring currently means. So the readout that shows the wiring is
 * the place that has to expand it. */
export function usesPreviousToken(stored: string): boolean {
  return PREVIOUS_RE.test(stored ?? '')
}

/** Which of a node's senders its *stored* prompt places itself.
 *
 * Every sender's output arrives either way -- the edge is the request. What
 * this decides is *where*: a sender named in the prompt is rendered inline at
 * the spot the author chose, and its automatic block is dropped so it is not
 * delivered twice. Mirrors `_hand_placed_sender_ids`.
 *
 * `{{previous}}` counts for every sender at once, because that is what it
 * expands to.
 *
 * A field reference counts too, unlike on the backend. That is not a drift:
 * the backend question is "may I suppress the whole block", and `{{node:a.x}}`
 * must not, because one extracted number is not the answer it came from. The
 * question here is "does this prompt mention this sender at all", asked so the
 * readout can offer the sender's declared field names -- and a prompt that
 * already uses one of them plainly does.
 */
export function referencedSenderIds(stored: string, receives: ReferenceTarget[]): Set<string> {
  const text = stored ?? ''
  if (PREVIOUS_RE.test(text)) return new Set(receives.map((t) => t.id))
  const referenced = new Set<string>()
  for (const match of text.matchAll(STORED_RE)) {
    if (receives.some((t) => t.id === match[1])) referenced.add(match[1])
  }
  return referenced
}

/** One recorded unresolved reference, as a name the user reads.
 *
 * The run records ids, and a field reference is recorded whole (`a.n_rows`)
 * because the gap is the field rather than the node -- so only the id half is
 * translated. Mirrors `experiment_run_results._reference_label`; node ids
 * cannot contain a dot, which is what makes the split safe.
 */
export function referenceLabel(ref: string, names: Record<string, string>): string {
  const dot = ref.indexOf('.')
  if (dot < 0) return names[ref] ?? ref
  const id = ref.slice(0, dot)
  return names[id] ? `${names[id]}${ref.slice(dot)}` : ref
}

/** Storage form -> display form. Ids become labels; everything else is left
 *  exactly as written, including a `{{node:...}}` whose node has been deleted
 *  (shown raw, because there is no name left to show and hiding it would hide
 *  the problem). */
export function toDisplayPrompt(stored: string, names: Record<string, string>): string {
  return toDisplayPromptWith(stored, (id) => names[id])
}

/** `toDisplayPrompt` against a lookup instead of a prebuilt map.
 *
 * For call sites that hold a lookup rather than a names object -- a canvas
 * node card resolves its own prompt on every React Flow store tick, including
 * every pointer move of a drag, and building a whole-graph names object there
 * would be O(nodes) work per node per tick. Resolving only the ids the prompt
 * actually names is O(refs), and the common case (no references at all) costs
 * one `replace` that matches nothing.
 */
export function toDisplayPromptWith(stored: string, labelOf: (id: string) => string | undefined): string {
  return (stored ?? '').replace(STORED_RE, (whole, id: string, field: string | undefined, raw?: string) => {
    const label = labelOf(id)
    return label ? `{{${label}${field ? `.${field}` : ''}${raw ? '|raw' : ''}}}` : whole
  })
}

/** Display form -> storage form. The inverse, and the one that runs on save.
 *
 * A `{{X}}` matching no node and no bare token is left verbatim rather than
 * dropped or guessed at: it is literal prompt text as far as the run is
 * concerned, and `unresolvedReferences` is what tells the user so.
 */
export function toStoredPrompt(display: string, names: Record<string, string>): string {
  const idsByName = new Map(Object.entries(names).map(([id, name]) => [name.toLowerCase(), id]))
  const isName = (candidate: string) => idsByName.has(candidate.toLowerCase())
  return (display ?? '').replace(DISPLAY_RE, (whole, target: string, raw?: string) => {
    if (BARE_TOKENS.has(target.trim().toLowerCase())) return whole
    const { name, field } = splitDisplayTarget(target, isName)
    const id = idsByName.get(name.toLowerCase())
    return id ? `{{node:${id}${field ? `.${field}` : ''}${raw ? '|raw' : ''}}}` : whole
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
  const isName = (candidate: string) => known.has(candidate.toLowerCase())
  const found: string[] = []
  for (const match of (display ?? '').matchAll(DISPLAY_RE)) {
    const target = match[1].trim()
    if (BARE_TOKENS.has(target.toLowerCase())) continue
    // Reported whole (`Analyst.n_rows`) when the *node* is unknown, since that
    // is what the user typed and what they have to fix. A known node with an
    // undeclared field is not this warning's business -- publish refuses it
    // with the declared list, which is more useful than "names no node".
    if (isName(splitDisplayTarget(target, isName).name)) continue
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
  const isName = (candidate: string) => known.has(candidate.toLowerCase())
  const found: string[] = []
  for (const match of (display ?? '').matchAll(DISPLAY_RE)) {
    const target = match[1].trim()
    if (BARE_TOKENS.has(target.toLowerCase())) continue
    const lowered = splitDisplayTarget(target, isName).name.toLowerCase()
    if (inScope.has(lowered)) continue
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
