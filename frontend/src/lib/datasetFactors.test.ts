import { describe, expect, it } from 'vitest'
import { reconcileFactorBaselines } from './factorBindings'
import { reconcileDatasetFactor, datasetFactorIssues, datasetFactorOwner } from './datasetFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Agent', dataset_selection: ['a'], factor_bindings: { dataset_selection: 'Datasets' } } },
    ...['a', 'b', 'c'].map((id) => ({ id, type: 'dataset', data: { label: id, config: { dataset_id: id, dataset_name: `Dataset ${id}` } } })),
  ],
  edges: ['a', 'b', 'c'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'dataset' })),
} as unknown as ProtocolGraph
const factor: DesignFactor = { name: 'Datasets', level_type: 'dataset_selection', levels: [['b'], ['a']], level_labels: ['Custom B', 'Custom A'] }

describe('dataset connector factors', () => {
  it('keeps all-or-none levels in their chosen order as connections change', () => {
    const toggle: DesignFactor = { name: 'Datasets', level_type: 'dataset_toggle', levels: [['a', 'b'], []], level_labels: ['With datasets', 'Without datasets'] }
    expect(reconcileDatasetFactor(toggle, graph, 'agent')).toEqual({ ...toggle, levels: [['a', 'b', 'c'], []] })
    const noneFirst = { ...toggle, levels: [[], ['a', 'b']], level_labels: ['Without datasets', 'With datasets'] }
    expect(reconcileDatasetFactor(noneFirst, { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }, 'agent')).toEqual({ ...noneFirst, levels: [[], ['a', 'c']] })
    expect(datasetFactorIssues({ ...graph, edges: [] }, [toggle])[0]).toContain('restore the factor group')
  })
  it('preserves ordered labels and appends newly connected datasets', () => {
    expect(reconcileDatasetFactor(factor, graph, 'agent')).toEqual({ ...factor, levels: [['b'], ['a'], ['c']], level_labels: ['Custom B', 'Custom A', 'Dataset c'] })
  })
  it('removes disconnected levels without replacing the first level from canvas data', () => {
    const changed = { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }
    expect(reconcileFactorBaselines({ factors: [factor] }, changed)[0]).toEqual({ ...factor, levels: [['a'], ['c']], level_labels: ['Custom A', 'Dataset c'] })
  })
  it('keeps an empty factor visible and reports incomplete and unavailable datasets', () => {
    const empty = { ...graph, edges: [] }
    expect(reconcileDatasetFactor(factor, empty, 'agent')).toEqual(factor)
    expect(datasetFactorIssues(empty, [factor])[0]).toContain('restore the factor group')
    expect(datasetFactorIssues(graph, [factor], new Set(['a', 'c']))[0]).toContain('unavailable')
    expect(datasetFactorOwner(graph, 'b')).toBe('Datasets')
  })
})
