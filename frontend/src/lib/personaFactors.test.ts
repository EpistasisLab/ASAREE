import { expect, it } from 'vitest'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { factorCreationFields } from '@/components/protocol/bindableFields'
import { reconcileFactorBaselines } from './factorBindings'
import { personaFactorIssues, reconcilePersonaFactor } from './personaFactors'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Agent', persona_selection: ['a'], factor_bindings: { persona_selection: 'Personas' } } },
    ...['a', 'b', 'c'].map(id => ({ id, type: 'persona', data: { label: `Persona ${id}`, config: { persona_id: 'deleted-library-id', persona_text: id } } })),
  ],
  edges: ['a', 'b', 'c'].map(id => ({ source: id, target: 'agent', targetHandle: 'persona' })),
} as unknown as ProtocolGraph
const factor: DesignFactor = { name: 'Personas', level_type: 'persona_selection', levels: [['b'], ['a']], level_labels: ['Custom B', 'Custom A'] }

it('reconciles connected snapshots while preserving level order and labels', () => {
  expect(reconcilePersonaFactor(factor, graph, 'agent')).toEqual({ ...factor, levels: [['b'], ['a'], ['c']], level_labels: ['Custom B', 'Custom A', 'Persona c'] })
  const changed = { ...graph, edges: graph.edges.filter(edge => edge.source !== 'b') }
  expect(reconcileFactorBaselines({ factors: [factor] }, changed)[0]).toEqual({ ...factor, levels: [['a'], ['c']], level_labels: ['Custom A', 'Persona c'] })
  expect(personaFactorIssues(graph, [factor])).toEqual([])
})

it('preserves an all-disabled baseline and updates the all-enabled level after wiring changes', () => {
  const toggle: DesignFactor = { name: 'Personas', level_type: 'persona_toggle', levels: [[], ['a', 'b']], level_labels: ['Without', 'With'] }
  expect(reconcilePersonaFactor(toggle, graph, 'agent')).toEqual({ ...toggle, levels: [[], ['a', 'b', 'c']] })
  expect(personaFactorIssues({ ...graph, edges: [] }, [toggle])[0]).toContain('restore the factor group')
})

it('offers the same three factor modes from Design as the node toolbar', () => {
  const fields = factorCreationFields(graph.nodes, graph.edges).filter(field => field.connectorFactor?.kind === 'persona')
  expect(new Set(fields.map(field => field.fieldLabel))).toEqual(new Set(['This persona on/off', 'Persona levels', 'All personas on/off']))
  expect(fields.filter(field => field.levelType === 'persona_selection')).toHaveLength(1)
})
