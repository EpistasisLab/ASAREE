import { describe, expect, it } from 'vitest'
import { applyCustomMetricChange } from './customMetrics'
import { removeMetricFromMeasurementPlan } from './measurementPlan'
import { nodeRuntimeBindingForMetric } from './nodeRuntimeMetrics'

describe('node runtime metrics', () => {
  it('writes one binding, with its facts input, per metric', () => {
    const tokens = { id: 'tokens-dc', name: 'tokens_dc', kind: 'custom' as const }
    const turns = { id: 'turns-dc', name: 'n_turns_dc', kind: 'custom' as const }
    let plan = applyCustomMetricChange(null, tokens, { producer: 'node_runtime', nodeIds: ['agent-dc'], output: 'total_tokens' }, undefined)
    plan = applyCustomMetricChange(plan, turns, { producer: 'node_runtime', nodeIds: ['agent-dc'], output: 'agent_loop_iterations' }, undefined)

    expect(plan.metrics).toContainEqual({ id: 'tokens-dc', name: 'tokens_dc', value_type: 'number', aggregation: 'sum', unit: 'tokens' })
    expect(nodeRuntimeBindingForMetric(plan, 'tokens-dc')).toMatchObject({
      producer_id: 'asaree.node_runtime',
      kind: 'runtime',
      outputs: { total_tokens: 'tokens-dc' },
      config: { node_ids: ['agent-dc'] },
    })
    expect(plan.inputs.map((input) => input.producer_binding_id)).toEqual(['node-runtime-tokens-dc', 'node-runtime-turns-dc'])

    const edited = applyCustomMetricChange(plan, tokens, { producer: 'node_runtime', nodeIds: ['gate-dc', 'gate-fs'], output: 'output_tokens' }, undefined)
    expect(nodeRuntimeBindingForMetric(edited, 'tokens-dc')).toMatchObject({
      outputs: { output_tokens: 'tokens-dc' },
      config: { node_ids: ['gate-dc', 'gate-fs'] },
    })
    expect(edited.producers).toHaveLength(2)

    const removed = removeMetricFromMeasurementPlan(edited, 'tokens-dc')
    expect(removed?.inputs.map((input) => input.producer_binding_id)).toEqual(['node-runtime-turns-dc'])
  })
})
