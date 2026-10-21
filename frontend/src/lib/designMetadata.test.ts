import { describe, expect, it } from 'vitest'
import type { Experiment, MeasurementPlan } from '@/types/experiments'
import { DesignMetadataConflictError, designMetadataOf, designMetadataPatch } from './designMetadata'

const emptyPlan: MeasurementPlan = { metrics: [], producers: [], inputs: [] }
const boundPlan: MeasurementPlan = {
  metrics: [{ id: 'pr-auc', name: 'pr_auc' }],
  producers: [{ id: 'score', producer_id: 'asaree.tool_step', kind: 'reported', outputs: { pr_auc: 'pr-auc' }, artifacts: [], config: {} }],
  inputs: [],
} as unknown as MeasurementPlan

function experiment(overrides: Partial<Experiment> = {}): Experiment {
  return {
    id: 'experiment-1',
    name: 'Spinal',
    description: null,
    hypothesis: null,
    design_type: 'factorial',
    task_brief: null,
    design_spec: {
      factors: [{ name: 'Model', levels: ['a', 'b'], level_type: 'string' }],
      replicates: 10,
      metrics: [{ id: 'pr-auc', name: 'pr_auc', kind: 'custom' }],
      coordination_strategy: { slug: 'critic_gate', params: {} },
    },
    measurement_plan: boundPlan,
    dataset_ids: [],
    dataset_id: null,
    locked_at: null,
    locked_protocol_revision_id: null,
    locked_design_spec: null,
    locked_measurement_plan: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
    archived_at: null,
    ...overrides,
  }
}

describe('designMetadataPatch', () => {
  it('sends nothing for a draft that was never edited, even when the server moved on', () => {
    // The clobber: a panel loaded with an empty plan, then a script bound the
    // metrics. The panel's untouched stale copy must not be written back.
    const stale = experiment({ measurement_plan: emptyPlan })
    const baseline = designMetadataOf(stale)
    expect(designMetadataPatch(baseline, baseline, experiment(), 'protocol-1')).toBeNull()
  })

  it('sends only the edited field and keeps the rest of the server design', () => {
    const baseline = designMetadataOf(experiment())
    const fresh = experiment({ design_spec: { ...experiment().design_spec, replicates: 20 } })
    const patch = designMetadataPatch({ ...baseline, hypothesis: '  Critic helps  ' }, baseline, fresh, 'protocol-1')
    expect(patch).toEqual({ hypothesis: 'Critic helps' })
  })

  it('merges an edited design field over the fresh design_spec', () => {
    const baseline = designMetadataOf(experiment())
    const fresh = experiment({ design_spec: { ...experiment().design_spec, replicates: 20 } })
    const patch = designMetadataPatch({ ...baseline, randomizationSeed: 7 }, baseline, fresh, 'protocol-1')
    expect(patch).toEqual({ design_spec: { ...fresh.design_spec, randomization_seed: 7 } })
    expect(patch).not.toHaveProperty('measurement_plan')
  })

  it('sends a measurement plan edit with the protocol to validate it against', () => {
    const baseline = designMetadataOf(experiment({ measurement_plan: emptyPlan }))
    const patch = designMetadataPatch({ ...baseline, measurementPlan: boundPlan }, baseline, experiment({ measurement_plan: emptyPlan }), 'protocol-1')
    expect(patch).toEqual({ measurement_plan: boundPlan, measurement_validation_protocol_id: 'protocol-1' })
  })

  it('refuses to overwrite a field another writer changed since the draft was seeded', () => {
    const baseline = designMetadataOf(experiment({ measurement_plan: emptyPlan }))
    const edited = { ...baseline, measurementPlan: { ...boundPlan, producers: [] } }
    expect(() => designMetadataPatch(edited, baseline, experiment(), 'protocol-1')).toThrow(DesignMetadataConflictError)
  })

  it('treats an edit the server already holds as saved, not as a conflict', () => {
    const baseline = designMetadataOf(experiment())
    const fresh = experiment({ hypothesis: 'same' })
    expect(designMetadataPatch({ ...baseline, hypothesis: 'same' }, baseline, fresh, null)).toBeNull()
  })
})
