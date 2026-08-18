import type { Edge, Node } from '@xyflow/react'
import type { LevelType } from './factorLevels'

export interface BindableFieldSpec {
  fieldPath: string
  label: string
  levelType: LevelType
}

// Picking a server for an MCP Tool node's Tool connector -- shared by
// McpToolNodeInspector's own Server select and FactorEditorDialog's
// tool_config structured level editor, both of which need the same "which
// tools end up allow-listed when the server changes" decision. Keeps
// whichever of the PREVIOUS allow-list's tool names still exist on the
// newly picked server (e.g. re-selecting a server after importing a
// protocol JSON, where tool_names was already set but server_id was null --
// the intended allow-list should survive that reselection intact) -- only
// falls back to "every tool enabled" when nothing carries over, which is
// exactly the case for a genuinely fresh node (empty tool_names) or a
// switch to a server with no name overlap at all.
export function pickToolNamesForServer(previousToolNames: string[], availableToolNames: string[]): string[] {
  const carried = previousToolNames.filter((name) => availableToolNames.includes(name))
  return carried.length > 0 ? carried : availableToolNames
}

// Whether a node has at least one field (including a whole-node factor like
// pattern_override/llm_config/tool_config/script_config, which is stored
// under factor_bindings the same way an ordinary field-path binding is --
// see bindableFieldsForNode's own comment) bound to an experimental factor.
// Every node component reads this straight off its own `data.factor_bindings`
// rather than needing anything threaded in from ProtocolCanvas.tsx.
export function hasBoundFactor(data: { factor_bindings?: Record<string, string> }): boolean {
  return boundFactorCount(data) > 0
}

// How many of them -- what NodeFactorBadge prints under its own glyph
// ("1 factor" / "3 factors"), so a node states outright how much of it varies
// across the design instead of only that something does. Same source of truth
// as hasBoundFactor above, which is now just this being non-zero.
export function boundFactorCount(data: { factor_bindings?: Record<string, string> }): number {
  return Object.keys(data.factor_bindings ?? {}).length
}

// The connector-type node families whose whole `config` (or, for Pattern, a
// synthetic `pattern_override`) can itself become a factor -- see each
// bindableFieldsForNode case below and the node-as-factor plan.
const LLM_NODE_TYPES = new Set(['llm_anthropic', 'llm_openai', 'llm_azure_foundry'])

// The Design tab's "Add factor" picker needs to know, for any node type,
// which fields are ever wrapped in a FactorBindableField "+" -- this is the
// single catalog both that picker and (implicitly) each node inspector's
// own FactorBindableField calls agree with, so the two never drift apart.
// Kept as a plain node-type switch rather than co-locating it inside each
// inspector component: the inspectors need live, capability-gated data
// (LlmNodeInspector's Temperature/Effort only show for models that support
// them) that isn't available outside that component's own query state, so
// this catalog deliberately lists every field a node type *could* ever
// bind, not only the ones currently visible on one specific node instance.
// Binding a field the current model doesn't support (e.g. Effort on a
// model without it) is harmless -- the value just isn't read.
//
// "active" (Agent only) isn't shown anywhere in AgentNodeInspector today --
// it's the canvas hover-toolbar's Power/PowerOff toggle instead -- but
// services.protocol_execution's apply_factor_bindings already applies a
// dotted path starting at the node's own `data` (not hardcoded to
// `data.config`), so `data.active` is already a real, working bindable
// field today with zero backend changes; only the frontend never offered
// a way to bind it until now.
//
// The whole-`config`/`pattern_override` entries below (llm_config/
// tool_config/pattern) are how a NODE itself becomes a factor -- e.g. an
// LLM node's levels can be entirely different provider+model+credential
// combinations, not just one scalar field varying inside an unchanging
// node. These are additive to (and mutually exclusive with, see
// unboundBindableFields) the ordinary per-field entries. Each also has its
// own inline "+" in its inspector (LlmNodeInspector's Credential row,
// McpToolNodeInspector's Server row -- both via FactorBindableField, which
// escalates straight to FactorEditorDialog for these 3 structured kinds),
// same as every other field; the Design tab's picker is just the other
// entry point onto the exact same binding.
export function bindableFieldsForNode(node: Node): BindableFieldSpec[] {
  switch (node.type) {
    case 'agent':
      return [
        { fieldPath: 'config.system_prompt', label: 'System prompt', levelType: 'text' },
        { fieldPath: 'active', label: 'Active', levelType: 'boolean' },
        // A synthetic field, not a real config value the frontend otherwise
        // reads -- see protocol_execution.py's _resolve_pattern_config,
        // which checks this before falling back to the wired connector
        // node. Lets a Pattern factor vary the node TYPE itself (Reason +
        // Act vs Single-Agent Baseline), which no ordinary field binding can
        // do since those are different node types with different config
        // shapes.
        { fieldPath: 'pattern_override', label: 'Execution pattern', levelType: 'pattern' },
      ]
    case 'critic_gate':
      return [{ fieldPath: 'config.enabled', label: 'Enabled', levelType: 'boolean' }]
    case 'llm_anthropic':
    case 'llm_openai':
    case 'llm_azure_foundry':
      return [
        { fieldPath: 'config.model', label: 'Model', levelType: 'string' },
        { fieldPath: 'config.temperature', label: 'Temperature', levelType: 'number' },
        { fieldPath: 'config.effort', label: 'Effort', levelType: 'string' },
        { fieldPath: 'config.max_tokens', label: 'Max tokens', levelType: 'number' },
        // The whole node as a factor -- levels are entirely different
        // provider+model+credential combinations. _resolve_llm_config reads
        // a connected LLM node's whole config verbatim (never the node's
        // xyflow `type`), so replacing it wholesale per cell already works
        // with zero backend changes.
        { fieldPath: 'config', label: 'Provider & model', levelType: 'llm_config' },
      ]
    case 'mcp_tool':
      return [
        { fieldPath: 'config.enabled', label: 'Enabled', levelType: 'boolean' },
        // The whole node as a factor -- levels can be entirely different MCP
        // servers (each with their own allowed tools). _resolve_tool_config
        // reads each wired mcp_tool node's own config fresh, so this already
        // works with zero backend changes.
        { fieldPath: 'config', label: 'Server & tools', levelType: 'tool_config' },
      ]
    case 'memory':
      // No runtime effect yet (Memory execution isn't implemented at all) --
      // ships as declared capability only, matching Memory's existing
      // status everywhere else in this codebase.
      return [{ fieldPath: 'config.enabled', label: 'Enabled', levelType: 'boolean' }]
    // Each pattern node type's OWN config fields -- distinct from the
    // agent's synthetic `pattern_override` above, which swaps the node TYPE
    // entirely. These vary a single param while keeping the same pattern
    // (e.g. how many iterations Reason+Act gets) -- already resolved
    // correctly with zero backend changes, since _resolve_pattern_config
    // reads the wired pattern node's own (already factor-patched) `data.config`
    // verbatim.
    case 'pattern_reason_act':
      return [
        { fieldPath: 'config.max_iterations', label: 'Max iterations', levelType: 'number' },
        { fieldPath: 'config.include_scratchpad', label: 'Include scratchpad', levelType: 'boolean' },
        { fieldPath: 'config.scratchpad_window', label: 'Scratchpad window', levelType: 'number' },
        { fieldPath: 'config.observation_format', label: 'Observation format', levelType: 'string' },
      ]
    case 'pattern_single_agent_baseline':
      return [
        { fieldPath: 'config.max_iterations', label: 'Max iterations', levelType: 'number' },
        { fieldPath: 'config.stop_on_first_success', label: 'Stop on first success', levelType: 'boolean' },
      ]
    case 'dataset':
      // No runtime effect from `enabled` beyond skipping the Dataset-context
      // block in _build_user_input -- no whole-node "swap the whole dataset
      // per cell" factor kind yet (a real, separable future capability,
      // deliberately deferred).
      return [{ fieldPath: 'config.enabled', label: 'Enabled', levelType: 'boolean' }]
    case 'script':
      // The whole node as a factor -- levels are entirely different scripts.
      // _resolve_script_config reads the wired script node's whole config
      // verbatim, so comparing two scoring scripts already works with zero
      // backend changes, same reasoning as llm_config/tool_config.
      return [{ fieldPath: 'config', label: 'Script', levelType: 'script_config' }]
    default:
      return []
  }
}

export interface UnboundField {
  nodeId: string
  nodeLabel: string
  fieldPath: string
  fieldLabel: string
  levelType: LevelType
  // The field's own current value (e.g. an Agent's actual config.system_prompt
  // text) -- lets FactorEditorDialog's field-picker seed the first level the
  // same way FactorBindableField's own popover already does, matching
  // services.protocol_execution's own apply_factor_bindings, which resolves
  // a dotted path starting at the node's whole `data` object. For
  // `pattern_override` specifically (a synthetic field with no real current
  // value under `data`), this is instead derived from whichever pattern
  // node the agent currently has wired (see derivePatternOverrideCurrentValue).
  currentValue: unknown
}

function getPath(data: Record<string, unknown>, dottedPath: string): unknown {
  let target: unknown = data
  for (const part of dottedPath.split('.')) {
    if (typeof target !== 'object' || target === null) return undefined
    target = (target as Record<string, unknown>)[part]
  }
  return target
}

// Mirrors protocol_execution.py's _EXECUTION_PATTERN_SLUGS -- the raw
// agentic-core slug each pattern node type resolves to.
const PATTERN_SLUGS: Record<string, string> = {
  pattern_reason_act: 'reason_act',
  pattern_single_agent_baseline: 'single_agent_baseline',
}

// The agent's CURRENTLY wired pattern, reshaped exactly like
// _resolve_pattern_config's own return value -- so binding "Execution
// pattern" as a factor starts from "the pattern you already have, plus
// whatever alternates you want to try," same seeding principle as every
// other field.
function derivePatternOverrideCurrentValue(agentNode: Node, edges: Edge[], nodes: Node[]): unknown {
  const edge = edges.find((e) => e.target === agentNode.id && e.targetHandle === 'architectural_pattern')
  if (!edge) return undefined
  const patternNode = nodes.find((n) => n.id === edge.source)
  const slug = patternNode ? PATTERN_SLUGS[patternNode.type ?? ''] : undefined
  if (!patternNode || !slug) return undefined
  return { execution_pattern: slug, pattern_params: { [slug]: (patternNode.data as { config?: unknown })?.config ?? {} } }
}

function isConnectorNodeType(type: string | undefined): boolean {
  return (
    type === 'memory' ||
    type === 'mcp_tool' ||
    type === 'dataset' ||
    type === 'script' ||
    LLM_NODE_TYPES.has(type ?? '') ||
    (type ?? '').startsWith('pattern_')
  )
}

// A connector node's own label alone doesn't say which agent it belongs to
// -- two different agents can each have an LLM node labeled "Anthropic," and
// the factor dialog otherwise can't tell them apart. Traces from the
// connector node to whichever single agent/critic_gate it's wired into (via
// the matching connector edge) and prefixes the label with that agent's own
// -- falls back to the plain label when unwired or fanned out to more than
// one agent (the existing rare/ambiguous case). Agent/critic_gate nodes
// themselves need no tracing -- they ARE the agent, not a connector.
//
// Colon-joined ("<agent>:<node>") so the eventual factor name (see
// computeFactorName, which appends ":<field>" to this) reads as one
// consistent "<agent>:<node>:<field>" chain throughout.
export function agentTracedLabel(node: Node, edges: Edge[], nodes: Node[]): string {
  const label = (node.data as { label?: string })?.label || node.type || 'Node'
  if (!isConnectorNodeType(node.type)) return label
  const targetIds = new Set(edges.filter((e) => e.source === node.id).map((e) => e.target))
  if (targetIds.size !== 1) return label
  const [targetId] = [...targetIds]
  const targetNode = nodes.find((n) => n.id === targetId)
  const targetLabel = (targetNode?.data as { label?: string })?.label || targetNode?.type
  return targetLabel ? `${targetLabel}:${label}` : label
}

// Every bindable field across the canvas that ISN'T already bound to a
// factor -- what the Design tab's "Add factor" picker lists. A field
// already bound just shows its "Factor: {name}" badge in the inspector
// instead of the "+" trigger, so it has nothing left to offer here either.
//
// Mutual exclusion: a node's whole `config` and any of its `config.*`
// sub-fields can never both be bound at once -- which one "wins" at
// substitution time would depend on factor_bindings iteration order, so
// this is prevented at the picker level entirely rather than relied on to
// "just work out." Once one side is bound, the other's entries are hidden
// here (the inspector's own already-bound field still shows its own
// "Factor: {name}" badge as normal; only the *unbound* opposite-side
// entries disappear from this picker).
export function unboundBindableFields(nodes: Node[], edges: Edge[]): UnboundField[] {
  const result: UnboundField[] = []
  for (const node of nodes) {
    const bindings = (node.data as { factor_bindings?: Record<string, string> })?.factor_bindings ?? {}
    const label = agentTracedLabel(node, edges, nodes)
    const wholeConfigBound = !!bindings.config
    const configSubFieldBound = Object.keys(bindings).some((path) => path !== 'config' && path.startsWith('config.'))
    for (const field of bindableFieldsForNode(node)) {
      if (bindings[field.fieldPath]) continue
      if (field.fieldPath === 'config' && configSubFieldBound) continue
      if (field.fieldPath.startsWith('config.') && wholeConfigBound) continue
      const currentValue =
        field.fieldPath === 'pattern_override'
          ? derivePatternOverrideCurrentValue(node, edges, nodes)
          : getPath(node.data as Record<string, unknown>, field.fieldPath)
      result.push({
        nodeId: node.id,
        nodeLabel: label,
        fieldPath: field.fieldPath,
        fieldLabel: field.label,
        levelType: field.levelType,
        currentValue,
      })
    }
  }
  return result
}
