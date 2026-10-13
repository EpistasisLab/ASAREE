import { describe, expect, it } from 'vitest'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'
import { factorCreationFields } from '@/components/protocol/bindableFields'
import { reconcileFactorBaselines } from './factorBindings'
import { graphWithSharedFactor, sharedFactorGroup, sharedFactorOwners, sharedGroupIssues, type SharedFactorKind } from './sharedFactors'

const families = [
  ['skill', 'skill', 'skill'], ['dataset', 'dataset', 'resource'],
  ['knowledge', 'okf_document', 'knowledge'], ['script', 'script', 'tool'],
  ['tool', 'mcp_tool', 'tool'], ['sub_agent', 'sub_agent', 'sub_agents'],
] as const

function graphFor(type: string, handle: string): ProtocolGraph {
  return {
    nodes: [
      ...['one', 'two'].map((id) => ({ id, type: 'agent', data: { label: id }, position: { x: 0, y: 0 } })),
      ...['a', 'b'].map((id) => ({ id, type, data: { label: id, config: { skill_id: id, dataset_id: id, document_id: id, code: 'print(1)', server_id: id } }, position: { x: 0, y: 0 } })),
    ],
    edges: ['a', 'b'].flatMap((source) => ['one', 'two'].map((target) => ({ id: `${source}-${target}`, source, target, targetHandle: handle }))),
  } as ProtocolGraph
}

describe.each(families)('shared %s groups', (kind, type, handle) => {
  const factor: DesignFactor = { name: 'Shared', level_type: `${kind}_selection`, levels: [['a'], ['b']] }
  it('binds every recipient and offers one group choice per factor type in New Factor', () => {
    const graph = graphFor(type, handle)
    const choices = factorCreationFields(graph.nodes, graph.edges).filter((field) => field.levelType === factor.level_type)
    expect(choices).toHaveLength(1)
    expect(choices[0].pickerGroup).toMatchObject({ id: 'shared:one,two', label: 'one, two' })
    const bound = graphWithSharedFactor(graph, 'a', kind, factor)
    expect(bound.nodes.slice(0, 2).map((node) => node.data.factor_bindings)).toEqual([{ [`${kind}_selection`]: 'Shared' }, { [`${kind}_selection`]: 'Shared' }])
    expect(sharedFactorOwners(bound, `${kind}_selection`)).toHaveLength(1)
    expect(sharedFactorOwners(bound, `${kind}_selection`, 'two')[0].id).toBe('two')
    expect(sharedGroupIssues(bound, [factor], kind)).toEqual([])
    expect(graph.nodes[0].data.factor_bindings).toBeUndefined()
  })

  it('blocks asymmetric alternatives from either source and preserves a bound factor after disconnecting', () => {
    const bound = graphWithSharedFactor(graphFor(type, handle), 'a', kind, factor)
    const changed = { ...bound, edges: bound.edges.filter((edge) => edge.id !== 'b-two') }
    for (const source of ['a', 'b']) expect(sharedFactorGroup(changed, source, kind).error).toContain('exactly the same Agents')
    expect(sharedGroupIssues(changed, [factor], kind)[0]).toContain('exactly the same Agents')
    expect(reconcileFactorBaselines({ factors: [factor] }, changed)).toEqual([factor])
  })

  it('flags a newly connected Agent and complete disconnection without losing the declaration', () => {
    const bound = graphWithSharedFactor(graphFor(type, handle), 'a', kind, factor)
    bound.nodes[1].data.factor_bindings = {}
    expect(sharedGroupIssues(bound, [factor], kind)[0]).toContain('every Agent')
    const disconnected = { ...bound, edges: [] }
    expect(reconcileFactorBaselines({ factors: [factor] }, disconnected)).toEqual([factor])
    expect(sharedGroupIssues(disconnected, [factor], kind)[0]).toContain('restore')
  })
})

it('shares whole Pattern configuration levels across all recipients', () => {
  const graph = graphFor('pattern_reason_act', 'architectural_pattern')
  graph.nodes = graph.nodes.filter((node) => node.id !== 'b')
  graph.edges = graph.edges.filter((edge) => edge.source !== 'b')
  const factor: DesignFactor = { name: 'Patterns', level_type: 'pattern', levels: [{ execution_pattern: 'reason_act', pattern_params: { reason_act: { max_iterations: 30 } } }, { execution_pattern: 'single_agent_baseline', pattern_params: { single_agent_baseline: { max_iterations: 20 } } }] }
  const bound = graphWithSharedFactor(graph, 'a', 'pattern', factor)
  expect(bound.nodes.slice(0, 2).every((node) => node.data.factor_bindings?.pattern_override === 'Patterns')).toBe(true)
  expect(sharedGroupIssues(bound, [factor], 'pattern')).toEqual([])
  const choices = factorCreationFields(graph.nodes, graph.edges)
  expect(choices.filter((field) => field.levelType === 'pattern')).toHaveLength(1)
  expect(choices.every((field) => field.connectorFactor?.kind !== 'pattern' || field.pickerGroup?.id === 'shared:one,two')).toBe(true)
})

it('requires separate unshared resource nodes for independent factor groups', () => {
  const graph = graphFor('skill', 'skill')
  graph.edges = graph.edges.filter((edge) => edge.id === 'a-one' || edge.id === 'b-two')
  const choices = factorCreationFields(graph.nodes, graph.edges).filter((field) => field.levelType === 'skill_toggle')
  expect(choices).toHaveLength(2)
  expect(sharedFactorGroup(graph, 'a', 'skill' as SharedFactorKind).error).toBeUndefined()
})
