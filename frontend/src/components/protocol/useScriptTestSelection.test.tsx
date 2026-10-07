import { act, renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { useScriptTestSelection } from './useScriptTestSelection'

it('uses per-agent defaults and resets choices when the scope changes', () => {
  const graph = { nodes: [
    { id: 'one', type: 'agent', data: { script_selection: ['a'], factor_bindings: { script_selection: 'One' } } },
    { id: 'two', type: 'sub_agent', data: { script_factor_mode: 'script_toggle', script_selection: [], factor_bindings: { script_selection: 'Two' } } },
    ...['a', 'b'].map((id) => ({ id, type: 'script', data: { config: { code: 'print(1)', enabled: false } } })),
  ], edges: ['one', 'two'].flatMap((target) => ['a', 'b'].map((source) => ({ source, target, targetHandle: 'tool' }))) } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const { result, rerender } = renderHook(({ nodeId }) => useScriptTestSelection(graph, nodeId), { initialProps: { nodeId: undefined as string | undefined } })
  expect(result.current.error).toBeNull()
  expect(result.current.options).toEqual({ script_selections: { one: 'a', two: 'none' } })
  act(() => result.current.setChoice('one', 'b'))
  expect(result.current.selections.one).toBe('b')
  rerender({ nodeId: 'one' })
  expect(result.current.selections).toEqual({ one: 'a' })
  expect(JSON.stringify(graph)).toBe(original)
})
