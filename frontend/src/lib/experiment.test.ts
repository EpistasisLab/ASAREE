import { describe, expect, it } from 'vitest'
import { displayCellLabel, displayFactorCondition, displayFactorLevel, displayFactorName } from './experiment'
import { normalizeDesignMetrics } from './metricCatalog'

describe('factor and cell display labels', () => {
  it('formats hierarchy and assignments without changing stored identities or level punctuation', () => {
    const values = { 'Azure AI Foundry:Model': 'GPT-4.1', 'PubMed Retrieval Agent:Prompt': 'Detailed: cited' }
    const stored = 'Azure AI Foundry:Model:GPT-4.1__PubMed Retrieval Agent:Prompt:Detailed: cited'
    expect(displayFactorName('Azure AI Foundry:Model')).toBe('Azure AI Foundry › Model')
    expect(displayCellLabel(values, undefined, stored)).toBe('Azure AI Foundry › Model = GPT-4.1 | PubMed Retrieval Agent › Prompt = Detailed: cited')
    expect(Object.keys(values)).toEqual(['Azure AI Foundry:Model', 'PubMed Retrieval Agent:Prompt'])
    expect(displayCellLabel({}, undefined, stored)).toBe(stored)
    expect(displayFactorCondition('Critic:Enabled', false, undefined)).toBe('Critic › Enabled = Disabled')
  })
})

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

describe('custom metric observations', () => {
  it('removes imported value semantics and does not use a custom metric as primary', () => {
    const metrics = normalizeDesignMetrics([{
      id: 'pr-auc',
      name: 'pr_auc',
      kind: 'custom',
      valueType: 'number',
      direction: 'maximize',
      aggregation: 'mean',
      primary: true,
    }])

    expect(metrics[0]).toEqual({ id: 'pr-auc', name: 'pr_auc', kind: 'custom' })
  })

  it('keeps only the identity and label of a custom draft', () => {
    const metric = normalizeDesignMetrics([{
      name: 'Reviewer report',
      kind: 'custom',
      direction: 'maximize',
      primary: true,
    }])[0]
    expect(metric).toEqual({ id: metric.id, name: 'Reviewer report', kind: 'custom' })
  })
})
