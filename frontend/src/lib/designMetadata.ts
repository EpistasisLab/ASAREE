import { normalizeDesignMetrics } from '@/lib/metricCatalog'
import type { CoordinationStrategySlug, DesignMetric, DesignSpec, Experiment, MeasurementPlan } from '@/types/experiments'

/** The Design tab's autosaved fields, in the shape its local draft holds them. */
export type DesignMetadata = {
  hypothesis: string
  randomizationSeed: number | null
  metrics: DesignMetric[]
  measurementPlan: MeasurementPlan | null
  coordinationSlug: CoordinationStrategySlug
}

export function designMetadataOf(experiment: Experiment): DesignMetadata {
  return {
    hypothesis: experiment.hypothesis ?? '',
    randomizationSeed: experiment.design_spec?.randomization_seed ?? null,
    metrics: normalizeDesignMetrics(experiment.design_spec?.metrics),
    measurementPlan: experiment.measurement_plan,
    coordinationSlug: experiment.design_spec?.coordination_strategy?.slug ?? 'sequential',
  }
}

/** Another writer (a script, the SDK, another tab) changed a field this draft also edited. */
export class DesignMetadataConflictError extends Error {
  readonly fields: Array<keyof DesignMetadata>

  constructor(fields: Array<keyof DesignMetadata>) {
    super('This design was changed elsewhere while you were editing it. It has been reloaded; re-apply your edit.')
    this.name = 'DesignMetadataConflictError'
    this.fields = fields
  }
}

type DesignMetadataPatch = {
  hypothesis?: string | null
  measurement_plan?: MeasurementPlan | null
  measurement_validation_protocol_id?: string | null
  design_spec?: DesignSpec
}

const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b)

/**
 * The PATCH body for a draft, or `null` when it edits nothing.
 *
 * Only fields the draft changed relative to `baseline` -- the server state it
 * was seeded from -- are sent; everything else keeps the server's current
 * value. Sending the whole draft let a panel loaded before an outside update
 * write its stale copy back over it. A field changed both here and on the
 * server since `baseline` is a conflict, not a last-writer-wins overwrite.
 */
export function designMetadataPatch(
  draft: DesignMetadata,
  baseline: DesignMetadata,
  fresh: Experiment,
  protocolId: string | null,
): DesignMetadataPatch | null {
  const current = designMetadataOf(fresh)
  const keys = Object.keys(draft) as Array<keyof DesignMetadata>
  const edited = keys.filter((key) => !same(draft[key], baseline[key]))
  const conflicts = edited.filter((key) => !same(current[key], baseline[key]) && !same(current[key], draft[key]))
  if (conflicts.length > 0) throw new DesignMetadataConflictError(conflicts)
  const pending = new Set(edited.filter((key) => !same(current[key], draft[key])))
  if (pending.size === 0) return null

  const patch: DesignMetadataPatch = {}
  if (pending.has('hypothesis')) patch.hypothesis = draft.hypothesis.trim() || null
  if (pending.has('measurementPlan')) {
    patch.measurement_plan = draft.measurementPlan
    patch.measurement_validation_protocol_id = protocolId
  }
  if (pending.has('metrics') || pending.has('randomizationSeed') || pending.has('coordinationSlug')) {
    const spec: DesignSpec = fresh.design_spec ?? {}
    patch.design_spec = {
      ...spec,
      ...(pending.has('randomizationSeed') ? { randomization_seed: draft.randomizationSeed } : {}),
      ...(pending.has('metrics') ? { metrics: draft.metrics.filter((metric) => metric.name.trim() !== '') } : {}),
      ...(pending.has('coordinationSlug')
        ? { coordination_strategy: { slug: draft.coordinationSlug, params: spec.coordination_strategy?.params ?? {} } }
        : {}),
    }
  }
  return patch
}
