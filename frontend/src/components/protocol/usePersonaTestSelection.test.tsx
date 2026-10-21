import { act, renderHook } from '@testing-library/react'
import { expect, it } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { usePersonaTestSelection } from './usePersonaTestSelection'

it('makes one preview choice per shared factor and resets when run scope changes', () => {
  const graph = {
    nodes: [
      ...['one', 'two'].map(id => ({ id, type: 'agent', data: { persona_selection: ['a'], factor_bindings: { persona_selection: 'Personas' } } })),
      ...['a', 'b'].map(id => ({ id, type: 'persona', data: { config: { persona_text: id, enabled: false } } })),
    ],
    edges: ['one', 'two'].flatMap(target => ['a', 'b'].map(source => ({ source, target, targetHandle: 'persona' }))),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const { result, rerender } = renderHook(({ nodeId }) => usePersonaTestSelection(graph, nodeId), { initialProps: { nodeId: undefined as string | undefined } })
  expect(result.current.selections).toEqual({ one: 'a' })
  expect(result.current.error).toBeNull()
  act(() => result.current.setChoice('one', 'b'))
  expect(result.current.options).toEqual({ persona_selections: { one: 'b' } })
  rerender({ nodeId: 'two' })
  expect(result.current.selections).toEqual({ two: 'a' })
  expect(JSON.stringify(graph)).toBe(original)
})
