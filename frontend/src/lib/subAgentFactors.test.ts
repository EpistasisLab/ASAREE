import { expect, it } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { reconcileSubAgentFactor, subAgentFactorIssues } from './subAgentFactors'

const graph = {
  nodes: [
    { id: 'parent', type: 'agent', data: { factor_bindings: { sub_agent_selection: 'Workers' } } },
    ...['a', 'b', 'c'].map((id) => ({ id, type: 'sub_agent', data: { label: `Worker ${id}` } })),
  ],
  edges: ['a', 'c'].map((source) => ({ source, target: 'parent', targetHandle: 'sub_agents' })),
} as unknown as ProtocolGraph

it('preserves level order and labels while dropping disconnected workers and adding new ones', () => {
  expect(reconcileSubAgentFactor({ name: 'Workers', level_type: 'sub_agent_selection', levels: [['b'], ['a']], level_labels: ['Old', 'Custom A'] }, graph, 'parent')).toEqual({
    name: 'Workers', level_type: 'sub_agent_selection', levels: [['a'], ['c']], level_labels: ['Custom A', 'Worker c'],
  })
})

it('keeps all-disabled as the baseline while refreshing the all-enabled level', () => {
  expect(reconcileSubAgentFactor({ name: 'Workers', level_type: 'sub_agent_toggle', levels: [[], ['b']], level_labels: ['Without', 'With'] }, graph, 'parent')).toEqual({
    name: 'Workers', level_type: 'sub_agent_toggle', levels: [[], ['a', 'c']], level_labels: ['Without', 'With'],
  })
})

it('reports incomplete selection and unbound factors', () => {
  const factor = { name: 'Workers', level_type: 'sub_agent_selection' as const, levels: [['a']] }
  expect(subAgentFactorIssues({ ...graph, edges: graph.edges.slice(0, 1) }, [factor])).toEqual(['Workers: connect at least two different sub-agents.'])
  expect(subAgentFactorIssues(undefined, [factor])).toEqual(['Workers: rebind or remove this sub-agent factor.'])
})
