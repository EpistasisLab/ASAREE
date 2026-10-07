import { act, renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { useSubAgentTestSelection } from './useSubAgentTestSelection'

it('keeps preview choices per parent and resets them when connections change', () => {
  const graph = {
    nodes: [
      { id: 'parent', type: 'agent', data: { factor_bindings: { sub_agent_selection: 'Workers' }, sub_agent_selection: ['a'] } },
      ...['a', 'b'].map((id) => ({ id, type: 'sub_agent', data: { label: id, active: false } })),
    ],
    edges: ['a', 'b'].map((source) => ({ source, target: 'parent', targetHandle: 'sub_agents' })),
  } as unknown as ProtocolGraph
  const { result, rerender } = renderHook(({ graph }) => useSubAgentTestSelection(graph), { initialProps: { graph } })
  expect(result.current.options).toEqual({ sub_agent_selections: { parent: 'a' } })
  act(() => result.current.setChoice('parent', 'b'))
  expect(result.current.options).toEqual({ sub_agent_selections: { parent: 'b' } })
  rerender({ graph: { ...graph, edges: graph.edges.slice(0, 1) } })
  expect(result.current.options).toEqual({ sub_agent_selections: { parent: 'a' } })
  expect(result.current.error).toBeNull()
})
