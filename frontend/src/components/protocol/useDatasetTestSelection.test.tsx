import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { datasetsApi } from '@/api/client'
import type { ProtocolGraph } from '@/types/protocols'
import { useDatasetTestSelection } from './useDatasetTestSelection'

afterEach(() => vi.restoreAllMocks())
it('uses all-or-none defaults per agent without altering shared dataset switches', async () => {
  vi.spyOn(datasetsApi, 'list').mockResolvedValue([{ id: 'a' }] as Awaited<ReturnType<typeof datasetsApi.list>>)
  const graph = {
    nodes: [
      { id: 'one', type: 'agent', data: { dataset_factor_mode: 'dataset_toggle', dataset_selection: ['a'], factor_bindings: { dataset_selection: 'One' } } },
      { id: 'two', type: 'agent', data: { dataset_factor_mode: 'dataset_toggle', dataset_selection: [], factor_bindings: { dataset_selection: 'Two' } } },
      { id: 'a', type: 'dataset', data: { config: { dataset_id: 'a', enabled: false } } },
    ],
    edges: ['one', 'two'].map((target) => ({ source: 'a', target, targetHandle: 'dataset' })),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(() => useDatasetTestSelection(graph), { wrapper })
  await waitFor(() => expect(result.current.error).toBeNull())
  expect(result.current.selections).toEqual({ one: 'all', two: 'none' })
  act(() => result.current.setChoice('one', 'none'))
  expect(result.current.options).toEqual({ dataset_selections: { one: 'none', two: 'none' } })
  expect(JSON.stringify(graph)).toBe(original)
})
it('keeps preview selections scoped to the agent and does not alter the first level', async () => {
  vi.spyOn(datasetsApi, 'list').mockResolvedValue([{ id: 'a' }, { id: 'b' }] as Awaited<ReturnType<typeof datasetsApi.list>>)
  const graph = {
    nodes: [
      { id: 'one', type: 'agent', data: { dataset_selection: ['a'], factor_bindings: { dataset_selection: 'One' } } },
      { id: 'two', type: 'agent', data: { dataset_selection: ['b'], factor_bindings: { dataset_selection: 'Two' } } },
      ...['a', 'b'].map((id) => ({ id, type: 'dataset', data: { config: { dataset_id: id } } })),
    ],
    edges: ['one', 'two'].flatMap((target) => ['a', 'b'].map((source) => ({ source, target, targetHandle: 'dataset' }))),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, rerender } = renderHook(({ nodeId }) => useDatasetTestSelection(graph, nodeId), { wrapper, initialProps: { nodeId: undefined as string | undefined } })
  await waitFor(() => expect(result.current.error).toBeNull())
  expect(result.current.selections).toEqual({ one: 'a', two: 'b' })
  act(() => result.current.setChoice('one', 'b'))
  expect(result.current.options).toEqual({ dataset_selections: { one: 'b', two: 'b' } })
  rerender({ nodeId: 'one' })
  expect(result.current.selections).toEqual({ one: 'a' })
  expect(JSON.stringify(graph)).toBe(original)
})
