import { STAGE_GATE_RULES, TABULAR_ML_STAGES, type DesignSpec, type StagePlanSpec, type StagePlanStage } from '@/types/experiments'

// A design-time mirror of asaree_workspace_core.stages, in the same spirit as
// lib/coordinationStrategy.ts: the backend owns what a stage plan means and is
// the only thing that validates one, but a malformed plan should say so under
// the editor rather than being rejected when the user clicks Generate cells.
//
// Deliberately NOT a second source of truth for the gate vocabulary -- the
// rules come from STAGE_GATE_RULES, which is the transcription of the
// backend's GATE_RULES, and everything here is derived from that list.

export const TABULAR_ML_PLAN_NAME = 'tabular_ml'

// Matches the backend's _STAGE_ID / _VERSION_ID.
const STAGE_ID = /^[a-z][a-z0-9_]{0,31}$/
const VERSION_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/

/** The preset the platform shipped with, as an editable starting point. */
export function tabularMlStages(): StagePlanStage[] {
  return TABULAR_ML_STAGES.map((stage) => ({ ...stage, gate: { ...(stage.gate ?? {}) } }))
}

/** True when this declaration is the built-in preset rather than a custom plan. */
export function isDefaultStagePlan(spec: DesignSpec | null | undefined): boolean {
  const declared = spec?.stage_plan
  if (!declared) return true
  if (typeof declared === 'string') return declared === TABULAR_ML_PLAN_NAME
  return declared.name === TABULAR_ML_PLAN_NAME && sameStages(declared.stages ?? [], TABULAR_ML_STAGES)
}

function sameStages(a: StagePlanStage[], b: StagePlanStage[]): boolean {
  return JSON.stringify(a.map(normalizeForCompare)) === JSON.stringify(b.map(normalizeForCompare))
}

function normalizeForCompare(stage: StagePlanStage) {
  return {
    id: stage.id,
    label: stage.label,
    version_id: stage.version_id ?? '',
    scratch: stage.scratch !== false,
    fixed_input: stage.fixed_input === true,
    gate: Object.fromEntries(Object.entries(stage.gate ?? {}).sort(([x], [y]) => x.localeCompare(y))),
  }
}

/**
 * The stages a design declares, always as an editable list. An absent or
 * preset declaration yields the preset's stages, so the editor never opens
 * empty and "Custom" starts from something that already works.
 */
export function stagesOf(spec: DesignSpec | null | undefined): StagePlanStage[] {
  const declared = spec?.stage_plan
  if (!declared || typeof declared === 'string') return tabularMlStages()
  const stages = declared.stages ?? []
  return stages.length > 0 ? stages.map((stage) => ({ ...stage, gate: { ...(stage.gate ?? {}) } })) : tabularMlStages()
}

/**
 * What to store in `design_spec.stage_plan`.
 *
 * The default is stored as `undefined`, not as an inline copy of the preset:
 * the backend only records a plan in a workspace's state.json when it differs
 * from the preset, which is what keeps a spinal-shaped workspace's state file
 * byte-shaped exactly as it was before plans existed. Writing an equivalent
 * inline copy would forfeit that for no gain.
 */
export function stagePlanDeclaration(custom: boolean, stages: StagePlanStage[]): StagePlanSpec | undefined {
  if (!custom) return undefined
  return { name: 'custom', stages: stages.map((stage, index) => withVersionId(stage, index)) }
}

/**
 * Version ids are derived from position + id rather than typed. They are path
 * components in the workspace's lineage, and a plan is fixed once a cell has
 * been staged through it, so there is nothing a hand-typed one buys -- while a
 * typo in one is a broken pipeline.
 */
export function withVersionId(stage: StagePlanStage, index: number): StagePlanStage {
  return { ...stage, version_id: `v${index + 1}_${stage.id}` }
}

/** Toggle one `rule:value` pair on a stage's gate. */
export function toggleGateRule(gate: Record<string, string>, key: string, value: string): Record<string, string> {
  const next = { ...gate }
  // The two `columns` rules share a key, so selecting one replaces the other
  // instead of stacking -- the backend would keep only the last anyway.
  if (next[key] === value) delete next[key]
  else next[key] = value
  return next
}

export function gateRuleLabels(gate: Record<string, string> | undefined): string[] {
  const entries = Object.entries(gate ?? {})
  return entries.map(([key, value]) => STAGE_GATE_RULES.find((r) => r.key === key && r.value === value)?.label ?? `${key}=${value}`)
}

/**
 * Everything wrong with a plan the user is editing, in the order the backend
 * would complain. Advisory: the list is shown under the editor and blocks
 * generation, rather than refusing the keystroke that caused it.
 */
export function stagePlanIssues(stages: StagePlanStage[]): string[] {
  const issues: string[] = []
  if (stages.length === 0) {
    issues.push('A stage plan needs at least one stage.')
    return issues
  }
  const seenIds = new Set<string>()
  for (const [index, stage] of stages.entries()) {
    const position = `Stage ${index + 1}`
    if (!stage.id.trim()) issues.push(`${position} needs an id.`)
    else if (!STAGE_ID.test(stage.id)) {
      issues.push(`${position}'s id "${stage.id}" must be lowercase letters, digits and underscores, starting with a letter.`)
    } else if (seenIds.has(stage.id)) issues.push(`Two stages both use the id "${stage.id}".`)
    else seenIds.add(stage.id)
    if (!stage.label.trim()) issues.push(`${position} needs a name.`)
    const derived = `v${index + 1}_${stage.id}`
    if (stage.id.trim() && STAGE_ID.test(stage.id) && !VERSION_ID.test(derived)) {
      issues.push(`${position} would produce an unusable version id ("${derived}").`)
    }
    for (const [key, value] of Object.entries(stage.gate ?? {})) {
      if (!STAGE_GATE_RULES.some((rule) => rule.key === key && rule.value === value)) {
        issues.push(`${position} declares a gate rule this platform doesn't have (${key}=${value}).`)
      }
    }
  }
  return issues
}
