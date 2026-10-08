import { describe, expect, it } from 'vitest'
import { reconcileFactorBaselines } from './factorBindings'
import { reconcileSkillFactor, skillFactorIssues, skillFactorOwner } from './skillFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Agent', skill_selection: ['a'], factor_bindings: { skill_selection: 'Skills' } } },
    ...['a', 'b', 'c'].map((id) => ({ id, type: 'skill', data: { label: id, config: { skill_id: id, skill_name: `Skill ${id}` } } })),
  ],
  edges: ['a', 'b', 'c'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'skill' })),
} as unknown as ProtocolGraph
const factor: DesignFactor = { name: 'Skills', level_type: 'skill_selection', levels: [['b'], ['a']], level_labels: ['Custom B', 'Custom A'] }

describe('skill connector factors', () => {
  it('keeps all-or-none levels in their chosen order as connections change', () => {
    const toggle: DesignFactor = { name: 'Skills', level_type: 'skill_toggle', levels: [['a', 'b'], []], level_labels: ['With skills', 'Without skills'] }
    expect(reconcileSkillFactor(toggle, graph, 'agent')).toEqual({ ...toggle, levels: [['a', 'b', 'c'], []] })
    const noneFirst = { ...toggle, levels: [[], ['a', 'b']], level_labels: ['Without skills', 'With skills'] }
    expect(reconcileSkillFactor(noneFirst, { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }, 'agent')).toEqual({ ...noneFirst, levels: [[], ['a', 'c']] })
    expect(skillFactorIssues({ ...graph, edges: [] }, [toggle])[0]).toContain('restore the factor group')
  })
  it('preserves ordered labels and appends newly connected skills', () => {
    expect(reconcileSkillFactor(factor, graph, 'agent')).toEqual({ ...factor, levels: [['b'], ['a'], ['c']], level_labels: ['Custom B', 'Custom A', 'Skill c'] })
  })
  it('removes disconnected levels without replacing the first level from canvas data', () => {
    const changed = { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }
    expect(reconcileFactorBaselines({ factors: [factor] }, changed)[0]).toEqual({ ...factor, levels: [['a'], ['c']], level_labels: ['Custom A', 'Skill c'] })
  })
  it('keeps an empty factor visible and reports incomplete and unavailable skills', () => {
    const empty = { ...graph, edges: [] }
    expect(reconcileSkillFactor(factor, empty, 'agent')).toEqual(factor)
    expect(skillFactorIssues(empty, [factor])[0]).toContain('restore the factor group')
    expect(skillFactorIssues(graph, [factor], new Set(['a', 'c']))[0]).toContain('unavailable')
    expect(skillFactorOwner(graph, 'b')).toBe('Skills')
  })
})
