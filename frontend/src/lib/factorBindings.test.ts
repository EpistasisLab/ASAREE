import { describe, expect, it } from 'vitest'
import { factorBindingDiscrepancies, graphWithFactorBaseline, reconcileFactorBaselines } from './factorBindings'
import type { ProtocolGraph } from '@/types/protocols'

describe('factorBindingDiscrepancies', () => {
  it('reports a changed canvas value before a cell run is selected', () => {
    const graph = {
      nodes: [{
        id: 'agent-1',
        type: 'agent',
        position: { x: 0, y: 0 },
        data: {
          label: 'Writer',
          factor_bindings: { 'config.system_prompt': 'Prompt' },
          config: { system_prompt: 'newly published prompt' },
        },
      }],
      edges: [],
    } as unknown as ProtocolGraph

    expect(factorBindingDiscrepancies(
      { factors: [{ name: 'Prompt', levels: ['original prompt', 'alternate prompt'] }] },
      graph,
    )).toEqual([{
      nodeId: 'agent-1',
      nodeLabel: 'Writer',
      fieldPath: 'config.system_prompt',
      factorName: 'Prompt',
      reason: 'published value does not match the first (canvas baseline) level',
    }])
  })

  it('keeps the current canvas value first while preserving alternate levels', () => {
    const graph = {
      nodes: [{
        id: 'agent-1',
        type: 'agent',
        position: { x: 0, y: 0 },
        data: {
          label: 'Writer',
          factor_bindings: { 'config.system_prompt': 'Prompt' },
          config: { system_prompt: 'edited canvas prompt' },
        },
      }],
      edges: [],
    } as unknown as ProtocolGraph

    expect(reconcileFactorBaselines({
      factors: [{
        name: 'Prompt',
        levels: ['old baseline', 'alternate'],
        level_labels: ['Original', 'Alternative'],
      }],
    }, graph)).toEqual([{
      name: 'Prompt',
      levels: ['edited canvas prompt', 'old baseline', 'alternate'],
      level_labels: ['Baseline', 'Original', 'Alternative'],
    }])
  })

  it('mirrors a changed baseline into every field sharing the factor', () => {
    const graph = {
      nodes: [
        { id: 'agent-1', type: 'agent', position: { x: 0, y: 0 }, data: { factor_bindings: { 'config.prompt': 'Prompt' }, config: { prompt: 'old' } } },
        { id: 'agent-2', type: 'agent', position: { x: 0, y: 0 }, data: { factor_bindings: { 'config.prompt': 'Prompt' }, config: { prompt: 'old' } } },
      ],
      edges: [],
    } as unknown as ProtocolGraph

    const updated = graphWithFactorBaseline(graph, 'Prompt', 'new baseline')
    expect(updated.nodes.map((node) => (node.data.config as { prompt: string }).prompt)).toEqual([
      'new baseline',
      'new baseline',
    ])
  })
})
