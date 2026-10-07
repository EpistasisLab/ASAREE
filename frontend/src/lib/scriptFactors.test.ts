import { describe, expect, it } from 'vitest'
import { reconcileFactorBaselines } from './factorBindings'
import { reconcileScriptFactor, requiredScriptIssues, scriptFactorIssues, scriptFactorOwner } from './scriptFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Agent', script_selection: ['a'], factor_bindings: { script_selection: 'Scripts' } } },
    ...['a', 'b', 'c'].map((id) => ({ id, type: 'script', data: { label: `Script ${id}`, config: { code: 'print(1)' } } })),
  ],
  edges: ['a', 'b', 'c'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'tool' })),
} as unknown as ProtocolGraph
const factor: DesignFactor = { name: 'Scripts', level_type: 'script_selection', levels: [['b'], ['a']], level_labels: ['Custom B', 'Custom A'] }

describe('script connector factors', () => {
  it.each(['', '   ', null])('reports an empty code treatment for a required Script (%s)', (code) => {
    const requiredGraph: ProtocolGraph = {
      nodes: [
        { id: 'step', type: 'tool_step', position: { x: 0, y: 0 }, data: { label: 'step', config: { tool_name: 'execute', arguments: { code: { source: 'script_code' } } } } },
        { id: 'script', type: 'script', position: { x: 0, y: 0 }, data: { label: 'Script', config: { name: 'Script', language: 'python', code: 'print(1)' }, factor_bindings: { 'config.code': 'Code' } } },
      ],
      edges: [{ id: 'script-step', source: 'script', target: 'step', targetHandle: 'tool' }],
    }
    expect(requiredScriptIssues(requiredGraph, [{ name: 'Code', levels: ['print(1)', code] }])).toEqual([
      'step: requires script_code; every Script code level needs code.',
    ])
    expect(requiredScriptIssues(requiredGraph, [{ name: 'Code', levels: ['print(1)', 'print(2)'] }])).toEqual([])
  })
  it('keeps all-or-none levels in their chosen order as connections change', () => {
    const toggle: DesignFactor = { name: 'Scripts', level_type: 'script_toggle', levels: [['a', 'b'], []], level_labels: ['With scripts', 'Without scripts'] }
    expect(reconcileScriptFactor(toggle, graph, 'agent')).toEqual({ ...toggle, levels: [['a', 'b', 'c'], []] })
    const noneFirst = { ...toggle, levels: [[], ['a', 'b']], level_labels: ['Without scripts', 'With scripts'] }
    expect(reconcileScriptFactor(noneFirst, { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }, 'agent')).toEqual({ ...noneFirst, levels: [[], ['a', 'c']] })
    expect(scriptFactorIssues({ ...graph, edges: [] }, [toggle])[0]).toContain('at least one')
  })
  it('preserves ordered labels and appends newly connected scripts', () => {
    expect(reconcileScriptFactor(factor, graph, 'agent')).toEqual({ ...factor, levels: [['b'], ['a'], ['c']], level_labels: ['Custom B', 'Custom A', 'Script c'] })
  })
  it('removes disconnected levels without replacing the first level from canvas data', () => {
    const changed = { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }
    expect(reconcileFactorBaselines({ factors: [factor] }, changed)[0]).toEqual({ ...factor, levels: [['a'], ['c']], level_labels: ['Custom A', 'Script c'] })
  })
  it('keeps an empty factor visible and reports incomplete and unavailable scripts', () => {
    const empty = { ...graph, edges: [] }
    expect(reconcileScriptFactor(factor, empty, 'agent').levels).toEqual([])
    expect(scriptFactorIssues(empty, [factor])[0]).toContain('at least two')
    expect(scriptFactorOwner(graph, 'b')).toBe('Scripts')
  })
})
