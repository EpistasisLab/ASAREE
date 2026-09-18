import { describe, expect, it } from 'vitest'
import { factorBindingDiscrepancies } from './factorBindings'
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
      reason: 'published value does not match any declared level',
    }])
  })
})
