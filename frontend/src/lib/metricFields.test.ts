import { describe, expect, it } from 'vitest'
import { removeMetricFromMeasurementPlan } from './measurementPlan'
import { bindingProjection, defaultFieldNames, observedFields } from './metricFields'
import { upsertToolStepMetric } from './toolStepMetrics'

describe('metric fields', () => {
  it('lists nested payload fields without splitting dotted keys', () => {
    const fields = observedFields({ test_metrics: { 'metrics_at_0.5': { f1: 0.4 } }, notes: ['a'] })
    expect(fields.map((field) => field.path)).toEqual([
      'test_metrics', 'test_metrics.metrics_at_0.5', 'test_metrics.metrics_at_0.5.f1', 'notes',
    ])
    expect(fields.find((field) => field.path === 'test_metrics.metrics_at_0.5.f1')?.segments).toEqual(['test_metrics', 'metrics_at_0.5', 'f1'])
    expect(fields.find((field) => field.path === 'notes')?.countable).toBe(true)
  })

  it('names picks by leaf key and widens only on collision', () => {
    const pick = (segments: string[], transform?: 'length') => ({ field: { segments }, projection: { path: segments.join('.'), ...(transform ? { transform } : {}) } })
    expect(defaultFieldNames([
      pick(['test_metrics', 'roc_auc']),
      pick(['val_metrics', 'roc_auc']),
      pick(['notes'], 'length'),
    ], ['other'])).toEqual(['test_metrics_roc_auc', 'val_metrics_roc_auc', 'notes_count'])
    expect(defaultFieldNames([pick(['roc_auc'])], ['roc_auc'])).toEqual(['roc_auc_2'])
  })

  it('round-trips a Tool Step projection and prunes it with the metric', () => {
    const plan = upsertToolStepMetric(null, { id: 'm1', name: 'auc', kind: 'custom' }, 'tool-1', { path: 'test_metrics.roc_auc' })
    const binding = plan.producers[0]
    expect(binding.config).toEqual({ node_id: 'tool-1', projections: { value: { path: 'test_metrics.roc_auc' } } })
    expect(bindingProjection(binding, 'm1')).toEqual({ path: 'test_metrics.roc_auc' })
    expect(removeMetricFromMeasurementPlan(plan, 'm1')?.producers ?? []).toHaveLength(0)
  })
})
