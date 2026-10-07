import { describe, expect, it } from 'vitest'
import { reconcileFactorBaselines } from './factorBindings'
import { reconcileToolFactor, toolFactorIssues, toolFactorOwner } from './toolFactors'
import type { DesignFactor } from '@/types/experiments'
import type { ProtocolGraph } from '@/types/protocols'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Agent', tool_selection: ['a'], factor_bindings: { tool_selection: 'Tools' } } },
    ...['a', 'b', 'c'].map((id) => ({ id, type: 'mcp_tool', data: { label: `Tool ${id}`, config: { server_id: id, tool_names: ['run'] } } })),
  ],
  edges: ['a', 'b', 'c'].map((id) => ({ id, source: id, target: 'agent', targetHandle: 'tool' })),
} as unknown as ProtocolGraph
const factor: DesignFactor = { name: 'Tools', level_type: 'tool_selection', levels: [['b'], ['a']], level_labels: ['Custom B', 'Custom A'] }

describe('tool connector factors', () => {
  it('keeps all-or-none levels in their chosen order as connections change', () => {
    const toggle: DesignFactor = { name: 'Tools', level_type: 'tool_toggle', levels: [['a', 'b'], []], level_labels: ['With tools', 'Without tools'] }
    expect(reconcileToolFactor(toggle, graph, 'agent')).toEqual({ ...toggle, levels: [['a', 'b', 'c'], []] })
    const noneFirst = { ...toggle, levels: [[], ['a', 'b']], level_labels: ['Without tools', 'With tools'] }
    expect(reconcileToolFactor(noneFirst, { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }, 'agent')).toEqual({ ...noneFirst, levels: [[], ['a', 'c']] })
    expect(toolFactorIssues({ ...graph, edges: [] }, [toggle])[0]).toContain('at least one')
  })
  it('preserves ordered labels and appends newly connected tools', () => {
    expect(reconcileToolFactor(factor, graph, 'agent')).toEqual({ ...factor, levels: [['b'], ['a'], ['c']], level_labels: ['Custom B', 'Custom A', 'Tool c'] })
  })
  it('removes disconnected levels without replacing the first level from canvas data', () => {
    const changed = { ...graph, edges: graph.edges.filter((edge) => edge.source !== 'b') }
    expect(reconcileFactorBaselines({ factors: [factor] }, changed)[0]).toEqual({ ...factor, levels: [['a'], ['c']], level_labels: ['Custom A', 'Tool c'] })
  })
  it('keeps an empty factor visible and reports incomplete and unavailable tools', () => {
    const empty = { ...graph, edges: [] }
    expect(reconcileToolFactor(factor, empty, 'agent').levels).toEqual([])
    expect(toolFactorIssues(empty, [factor])[0]).toContain('at least two')
    expect(toolFactorIssues(graph, [factor], new Set(['a', 'c']))[0]).toContain('unavailable')
    expect(toolFactorOwner(graph, 'b')).toBe('Tools')
  })
})
