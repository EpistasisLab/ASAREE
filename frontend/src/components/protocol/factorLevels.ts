import type { DesignFactor } from '@/types/experiments'

// llm_config/tool_config/pattern/script_config/dataset_config are the "whole
// node as a factor" kinds (see bindableFields.ts) -- their levels are OBJECTS
// (a whole LLM/Tool/Script/Dataset node config, or a {execution_pattern,
// pattern_params} payload), never strings, unlike every other kind here.
//
// tool_names is the odd one out: its levels are ARRAYS of bare tool names,
// not objects and not a whole node config -- it binds one MCP node's
// `config.tool_names` allow-list, leaving the node's own server pinned. It
// groups with the structured kinds below only because its levels aren't
// strings either (see isStructuredLevelType).
export type LevelType =
  | 'string'
  | 'text'
  | 'number'
  | 'boolean'
  | 'llm_config'
  | 'tool_config'
  | 'pattern'
  | 'script_config'
  | 'dataset_config'
  | 'tool_names'

export const LEVEL_TYPE_LABELS: Record<LevelType, string> = {
  string: 'String',
  text: 'Long text',
  number: 'Number',
  boolean: 'Boolean',
  llm_config: 'Provider & model',
  tool_config: 'Server & tools',
  pattern: 'Execution pattern',
  script_config: 'Script',
  dataset_config: 'Dataset',
  tool_names: 'Tools allowed',
}

// Whether a level of this kind is a structured value (an object, or -- for
// tool_names -- a list) rather than a plain string. FactorEditorDialog's
// `levels` state switches its element type based on this, and
// FactorBindableField uses it to escalate straight to that dialog instead of
// its own one-line-Input popover.
export function isStructuredLevelType(type: LevelType): boolean {
  return (
    type === 'llm_config' ||
    type === 'tool_config' ||
    type === 'pattern' ||
    type === 'script_config' ||
    type === 'dataset_config' ||
    type === 'tool_names'
  )
}

export function levelTypeOf(factor: DesignFactor): LevelType {
  return factor.level_type ?? 'string'
}

export function defaultFactorLevelLabels(_factorName: string, count: number): string[] {
  return Array.from({ length: count }, (_, index) => `level${index + 1}`)
}

export function factorLevelLabels(factor: DesignFactor): string[] {
  const defaults = defaultFactorLevelLabels(factor.name, factor.levels.length)
  return factor.level_labels?.length === factor.levels.length
    ? factor.level_labels.map((label, index) => label.trim() || defaults[index])
    : defaults
}

// Shared visual identity for every "make experimental factor" trigger --
// FactorBindableField's own per-field triggers plus the Agent/Pattern
// inspectors' title-row buttons (the one spot that opens the per-node
// picker rather than binding a single field directly). A dedicated hue
// (violet, --chart-2) not already claimed by another meaning elsewhere in
// this app (chart-3 = fully scored, chart-4 = generated-but-unscored,
// chart-5 ≈ destructive's own hue, primary = every other button) -- fixed,
// not hashed, since this identifies a category of action, not a
// per-instance thing the way e.g. AgentCard's model-hue tint does. Pairs
// with the button's own `default` variant (bg-primary/text-primary-foreground/
// border-transparent already come from that variant + the shared base
// classes) -- this just overrides the background/glow color from primary
// to chart-2, so it reads as solid and deliberate rather than a bordered
// secondary action. text-[0.65rem] overrides the `xs` size's own text-xs
// (12px) explicitly -- the "Make factor" text needs to read as a small
// caption next to the field label it sits beside, not as body-sized text.
export const FACTOR_TRIGGER_CLASSNAME =
  'bg-chart-2 text-[0.65rem] shadow-[0_0_16px_-4px_var(--chart-2)] hover:bg-chart-2/80 hover:shadow-[0_0_20px_-3px_var(--chart-2)]'

export function parseLevelValue(raw: string, type: LevelType): unknown {
  return type === 'number' ? Number(raw) : raw
}

// A blank starting point for one structured level -- shaped exactly like
// what protocol_execution.py's _resolve_llm_config/_resolve_tool_config/
// _resolve_pattern_config already expect, so a freshly-added level is
// immediately a valid (if unconfigured) whole-node config rather than an
// empty object the executor can't do anything with.
export function emptyStructuredLevel(type: LevelType): unknown {
  switch (type) {
    case 'llm_config':
      return { provider: 'anthropic', model: '', temperature: 0.7, max_tokens: 128000 }
    case 'tool_config':
      return { server_id: null, server_name: null, tool_names: [], enabled: true }
    case 'pattern':
      return { execution_pattern: 'reason_act', pattern_params: { reason_act: {} } }
    case 'script_config':
      return { name: 'script', language: 'python', code: '' }
    // Both id and name, because both are read: the executor resolves the
    // dataset by NAME (_resolve_dataset_configs -> seed_cell_workspace),
    // while the canvas's own syncExperimentDatasets records the ID on the
    // experiment_datasets join row. DatasetConfigLevelRow's picker always
    // writes the pair together, never one alone.
    case 'dataset_config':
      return { dataset_id: null, dataset_name: null, enabled: true }
    // An empty allow-list, not a copy of the node's current one: _resolve_tool_config
    // contributes nothing for a node whose tool_names is empty, so a blank level
    // is the meaningful "this server's tools withheld for this cell" baseline
    // rather than an unconfigured placeholder.
    case 'tool_names':
      return []
    default:
      return ''
  }
}

// A boolean factor's levels are always exactly [true, false] -- there's
// nothing to type in, so switching to it replaces whatever was there.
// Every other type starts with two blank rows, matching this app's existing
// "start with a couple of empty slots" convention (FactorBindableField's
// own popover).
export function defaultLevelsForType(type: LevelType): unknown[] {
  if (type === 'boolean') return [true, false]
  if (isStructuredLevelType(type)) return [emptyStructuredLevel(type), emptyStructuredLevel(type)]
  return ['', '']
}

// Seeds a fresh factor's first level with the field's own current value --
// e.g. binding an agent's already-written system prompt starts that factor
// at "the prompt you already have, plus whatever alternates you want to
// try" rather than making you retype it just to get back to today's value.
// Shared by FactorBindableField.tsx's own popover/dialog and
// FactorEditorDialog.tsx's field-picker (DesignTab's "Add factor"), so
// creating a factor seeds the same way regardless of entry point.
export function seedLevels(currentValue: unknown): string[] {
  const first = currentValue !== null && currentValue !== undefined && currentValue !== '' ? String(currentValue) : ''
  return [first, '']
}

// Structured counterpart of seedLevels -- the current value is already the
// right shape (a whole config/pattern-override object, not a scalar to
// stringify), so it's used verbatim as the first level.
export function seedStructuredLevels(currentValue: unknown, type: LevelType): unknown[] {
  const first = currentValue ?? emptyStructuredLevel(type)
  return [first, emptyStructuredLevel(type)]
}

// A factor bound to "System prompt" on Agent A and a factor bound to
// "System prompt" on Agent B are never the same thing -- they don't need to
// share levels, and one node's field being bound must never silently
// overwrite another's just because they happened to get the same default
// label. Scoping the name to its owning node (by label, not id -- ids make
// unreadable factor names) is what actually prevents that collision; the
// numeric suffix only kicks in for the rarer case of two nodes sharing the
// exact same current label (e.g. two never-renamed "Agent" nodes).
//
// Colon-joined, "<agent>:<node>:<field>" -- `nodeLabel` is already the
// agent-traced label for a connector node (see bindableFields.ts's
// agentTracedLabel, e.g. "Research Agent:Anthropic"), or just the agent's
// own label when the field lives on the agent itself (e.g. System prompt),
// so this always ends up fully qualified down to the owning agent.
export function computeFactorName(nodeLabel: string, fieldLabel: string, existingNames: string[]): string {
  const base = `${nodeLabel}:${fieldLabel}`
  if (!existingNames.includes(base)) return base
  let suffix = 2
  while (existingNames.includes(`${base} (${suffix})`)) suffix++
  return `${base} (${suffix})`
}
