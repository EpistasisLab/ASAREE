import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { skillsApi } from '@/api/client'
import type { ProtocolGraph } from '@/types/protocols'
import { useSkillTestSelection } from './useSkillTestSelection'

afterEach(() => vi.restoreAllMocks())
it('uses all-or-none defaults per agent without altering shared skill switches', async () => {
  vi.spyOn(skillsApi, 'list').mockResolvedValue([{ id: 'a' }] as Awaited<ReturnType<typeof skillsApi.list>>)
  const graph = {
    nodes: [
      { id: 'one', type: 'agent', data: { skill_factor_mode: 'skill_toggle', skill_selection: ['a'], factor_bindings: { skill_selection: 'One' } } },
      { id: 'two', type: 'agent', data: { skill_factor_mode: 'skill_toggle', skill_selection: [], factor_bindings: { skill_selection: 'Two' } } },
      { id: 'a', type: 'skill', data: { config: { skill_id: 'a', enabled: false } } },
    ],
    edges: ['one', 'two'].map((target) => ({ source: 'a', target, targetHandle: 'skill' })),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(() => useSkillTestSelection(graph), { wrapper })
  await waitFor(() => expect(result.current.error).toBeNull())
  expect(result.current.selections).toEqual({ one: 'all', two: 'none' })
  act(() => result.current.setChoice('one', 'none'))
  expect(result.current.options).toEqual({ skill_selections: { one: 'none', two: 'none' } })
  expect(JSON.stringify(graph)).toBe(original)
})
it('keeps preview selections scoped to the agent and does not alter the first level', async () => {
  vi.spyOn(skillsApi, 'list').mockResolvedValue([{ id: 'a' }, { id: 'b' }] as Awaited<ReturnType<typeof skillsApi.list>>)
  const graph = {
    nodes: [
      { id: 'one', type: 'agent', data: { skill_selection: ['a'], factor_bindings: { skill_selection: 'One' } } },
      { id: 'two', type: 'agent', data: { skill_selection: ['b'], factor_bindings: { skill_selection: 'Two' } } },
      ...['a', 'b'].map((id) => ({ id, type: 'skill', data: { config: { skill_id: id } } })),
    ],
    edges: ['one', 'two'].flatMap((target) => ['a', 'b'].map((source) => ({ source, target, targetHandle: 'skill' }))),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, rerender } = renderHook(({ nodeId }) => useSkillTestSelection(graph, nodeId), { wrapper, initialProps: { nodeId: undefined as string | undefined } })
  await waitFor(() => expect(result.current.error).toBeNull())
  expect(result.current.selections).toEqual({ one: 'a', two: 'b' })
  act(() => result.current.setChoice('one', 'b'))
  expect(result.current.options).toEqual({ skill_selections: { one: 'b', two: 'b' } })
  rerender({ nodeId: 'one' })
  expect(result.current.selections).toEqual({ one: 'a' })
  expect(JSON.stringify(graph)).toBe(original)
})
