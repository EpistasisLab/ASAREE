import { describe, expect, it } from 'vitest'
import { reconcileFactorBaselines } from './factorBindings'
import { reconcileKnowledgeFactor, knowledgeFactorIssues, knowledgeFactorOwner } from './knowledgeFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Agent', knowledge_selection: ['a'], factor_bindings: { knowledge_selection: 'Knowledge' } } },
    ...['a', 'b', 'c'].map((id) => ({ id, type: id === 'b' ? 'okf_document' : 'okf_bundle', data: { label: `Knowledge ${id}`, config: id === 'b' ? { document_id: id } : { bundle_id: id } } })),
  ],
  edges: ['a', 'b', 'c'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'knowledge' })),
} as unknown as ProtocolGraph
const factor: DesignFactor = { name: 'Knowledge', level_type: 'knowledge_selection', levels: [['b'], ['a']], level_labels: ['Custom B', 'Custom A'] }

describe('knowledge connector factors', () => {
  it('keeps all-or-none levels in their chosen order as connections change', () => {
    const toggle: DesignFactor = { name: 'Knowledge', level_type: 'knowledge_toggle', levels: [['a', 'b'], []], level_labels: ['With knowledge', 'Without knowledge'] }
    expect(reconcileKnowledgeFactor(toggle, graph, 'agent')).toEqual({ ...toggle, levels: [['a', 'b', 'c'], []] })
    const noneFirst = { ...toggle, levels: [[], ['a', 'b']], level_labels: ['Without knowledge', 'With knowledge'] }
    expect(reconcileKnowledgeFactor(noneFirst, { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }, 'agent')).toEqual({ ...noneFirst, levels: [[], ['a', 'c']] })
    expect(knowledgeFactorIssues({ ...graph, edges: [] }, [toggle])[0]).toContain('restore the factor group')
  })
  it('preserves ordered labels and appends newly connected knowledge', () => {
    expect(reconcileKnowledgeFactor(factor, graph, 'agent')).toEqual({ ...factor, levels: [['b'], ['a'], ['c']], level_labels: ['Custom B', 'Custom A', 'Knowledge c'] })
  })
  it('removes disconnected levels without replacing the first level from canvas data', () => {
    const changed = { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }
    expect(reconcileFactorBaselines({ factors: [factor] }, changed)[0]).toEqual({ ...factor, levels: [['a'], ['c']], level_labels: ['Custom A', 'Knowledge c'] })
  })
  it('keeps an empty factor visible and reports incomplete and unavailable knowledge', () => {
    const empty = { ...graph, edges: [] }
    expect(reconcileKnowledgeFactor(factor, empty, 'agent')).toEqual(factor)
    expect(knowledgeFactorIssues(empty, [factor])[0]).toContain('restore the factor group')
    expect(knowledgeFactorIssues(graph, [factor], new Set(['a', 'c']))[0]).toContain('unavailable')
    expect(knowledgeFactorOwner(graph, 'b')).toBe('Knowledge')
  })
})
