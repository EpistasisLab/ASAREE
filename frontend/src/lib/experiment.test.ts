import { describe, expect, it } from 'vitest'
import type { Experiment } from '@/types/experiments'
import { displayFactorLevel, primaryMetric } from './experiment'
import { normalizeDesignMetrics } from './metricCatalog'

describe('displayFactorLevel', () => {
  it('uses the label paired with a matching raw level', () => {
    expect(displayFactorLevel({
      factors: [{
        name: 'Agent:Prompt',
        levels: ['Write a complete technical response with citations.'],
        level_labels: ['Cited technical response'],
      }],
    }, 'Agent:Prompt', 'Write a complete technical response with citations.')).toBe('Cited technical response')
  })

  it('falls back to the raw value for unlabelled or unknown levels', () => {
    expect(displayFactorLevel({ factors: [{ name: 'Mode', levels: ['full'] }] }, 'Mode', 'full')).toBe('full')
    expect(displayFactorLevel({ factors: [{ name: 'Mode', levels: ['full'], level_labels: ['Complete'] }] }, 'Mode', 'brief')).toBe('brief')
  })

  it('retains a generated cell\'s label after its level content is edited', () => {
    const designSpec = {
      factors: [{
        name: 'Agent:Prompt',
        levels: ['A newly edited, very long prompt.'],
        level_labels: ['Cited technical response'],
      }],
    }
    expect(displayFactorLevel(
      designSpec,
      'Agent:Prompt',
      'The prior, very long prompt stored on this generated cell.',
      'Agent:Prompt:Cited technical response',
    )).toBe('Cited technical response')
  })
})

describe('reported scalar metrics', () => {
  it('preserves an explicitly typed custom metric and uses it as primary', () => {
    const metrics = normalizeDesignMetrics([{
      id: 'pr-auc',
      name: 'pr_auc',
      kind: 'custom',
      valueType: 'number',
      direction: 'maximize',
      aggregation: 'mean',
      primary: true,
    }])
    const experiment = { design_spec: { metrics } } as Experiment

    expect(metrics[0]).toMatchObject({ valueType: 'number', direction: 'maximize', primary: true })
    expect(primaryMetric(experiment)).toEqual({ key: 'pr_auc', direction: 'maximize', valueType: 'number' })
  })

  it('keeps an ordinary custom draft opaque and non-primary', () => {
    expect(normalizeDesignMetrics([{
      name: 'Reviewer report',
      kind: 'custom',
      direction: 'maximize',
      primary: true,
    }])[0]).toMatchObject({ valueType: 'opaque', direction: 'neutral', primary: false })
  })
})
