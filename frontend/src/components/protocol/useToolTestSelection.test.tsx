import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, renderHook, waitFor } from '@testing-library/react'
import type { ReactNode } from 'react'
import { afterEach, expect, it, vi } from 'vitest'
import { mcpServersApi } from '@/api/client'
import type { ProtocolGraph } from '@/types/protocols'
import { useToolTestSelection } from './useToolTestSelection'

afterEach(() => vi.restoreAllMocks())
it('uses all-or-none defaults once per shared factor without altering shared tool switches', async () => {
  vi.spyOn(mcpServersApi, 'list').mockResolvedValue([{ id: 'a', capabilities: { tools: [{ name: 'run' }] } }] as Awaited<ReturnType<typeof mcpServersApi.list>>)
  const graph = {
    nodes: [
      { id: 'one', type: 'agent', data: { tool_factor_mode: 'tool_toggle', tool_selection: ['a'], factor_bindings: { tool_selection: 'One' } } },
      { id: 'two', type: 'agent', data: { tool_factor_mode: 'tool_toggle', tool_selection: ['a'], factor_bindings: { tool_selection: 'One' } } },
      { id: 'a', type: 'mcp_tool', data: { config: { server_id: 'a', tool_names: ['run'], enabled: false } } },
    ],
    edges: ['one', 'two'].map((target) => ({ source: 'a', target, targetHandle: 'tool' })),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result } = renderHook(() => useToolTestSelection(graph), { wrapper })
  await waitFor(() => expect(result.current.error).toBeNull())
  expect(result.current.selections).toEqual({ one: 'all' })
  act(() => result.current.setChoice('one', 'none'))
  expect(result.current.options).toEqual({ tool_selections: { one: 'none' } })
  expect(JSON.stringify(graph)).toBe(original)
})
it('keeps a shared preview choice scoped to the requested agent and does not alter the first level', async () => {
  vi.spyOn(mcpServersApi, 'list').mockResolvedValue(['a', 'b'].map((id) => ({ id, capabilities: { tools: [{ name: 'run' }] } })) as Awaited<ReturnType<typeof mcpServersApi.list>>)
  const graph = {
    nodes: [
      { id: 'one', type: 'agent', data: { tool_selection: ['a'], factor_bindings: { tool_selection: 'One' } } },
      { id: 'two', type: 'agent', data: { tool_selection: ['a'], factor_bindings: { tool_selection: 'One' } } },
      ...['a', 'b'].map((id) => ({ id, type: 'mcp_tool', data: { config: { server_id: id, tool_names: ['run'] } } })),
    ],
    edges: ['one', 'two'].flatMap((target) => ['a', 'b'].map((source) => ({ source, target, targetHandle: 'tool' }))),
  } as unknown as ProtocolGraph
  const original = JSON.stringify(graph)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
  const { result, rerender } = renderHook(({ nodeId }) => useToolTestSelection(graph, nodeId), { wrapper, initialProps: { nodeId: undefined as string | undefined } })
  await waitFor(() => expect(result.current.error).toBeNull())
  expect(result.current.selections).toEqual({ one: 'a' })
  act(() => result.current.setChoice('one', 'b'))
  expect(result.current.options).toEqual({ tool_selections: { one: 'b' } })
  rerender({ nodeId: 'one' })
  expect(result.current.selections).toEqual({ one: 'a' })
  expect(JSON.stringify(graph)).toBe(original)
})
