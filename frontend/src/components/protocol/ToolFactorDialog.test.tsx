import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { expect, it, vi } from 'vitest'
import type { ProtocolGraph } from '@/types/protocols'
import { ToolFactorDialog } from './ToolFactorDialog'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { mcpServersApi } from '@/api/client'

const graph = {
  nodes: [
    { id: 'agent', type: 'agent', data: { label: 'Writer' } },
    { id: 'tool', type: 'mcp_tool', data: { label: 'Summarize', config: { server_id: 'a' }, factor_bindings: { 'config.enabled': 'Tool enabled' } } },
  ],
  edges: [{ source: 'tool', target: 'agent', targetHandle: 'tool' }],
} as unknown as ProtocolGraph

it('keeps the dialog open after removal and permits choosing another factor type', async () => {
  const remove = vi.fn().mockResolvedValue(undefined)
  const close = vi.fn()
  const { rerender } = render(<ToolFactorDialog toolNodeId="tool" graph={graph} factors={[{ name: 'Tool enabled', level_type: 'boolean', levels: [false, true] }]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  fireEvent.click(screen.getByRole('button', { name: 'Remove factor' }))
  await waitFor(() => expect(remove).toHaveBeenCalledWith('Tool enabled'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Cancel' })).toBeEnabled())
  expect(close).not.toHaveBeenCalled()
  const updated = { ...graph, nodes: graph.nodes.map((node) => ({ ...node, data: { ...node.data, factor_bindings: {} } })) }
  rerender(<ToolFactorDialog toolNodeId="tool" graph={updated} factors={[]} onClose={close} onSave={vi.fn()} onRemove={remove} />)
  expect(screen.queryByRole('button', { name: 'Remove factor' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Tool levels' }))
  expect(screen.getByLabelText('Factor name')).toBeEnabled()
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
})

it.each(['Tool levels', 'All tools on/off'])('explains why %s conflicts with individual bindings without offering replacement', (mode) => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<ToolFactorDialog toolNodeId="tool" graph={graph} factors={[{ name: 'Tool enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  const option = screen.getByRole('button', { name: mode })
  expect(option).toHaveClass('bg-secondary', 'text-secondary-foreground', 'border-dashed')
  expect(option).not.toHaveClass('opacity-50', 'text-muted-foreground')
  fireEvent.click(option)
  expect(option).toHaveClass('bg-primary', 'text-primary-foreground')
  expect(option).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
  expect(screen.getByRole('status')).toHaveTextContent('Tool enabled')
  expect(screen.getByRole('status')).toHaveTextContent('remove those bindings in each Tool’s factor dialog first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  expect(save).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'This tool on/off' }))
  expect(screen.getByLabelText('Factor name')).toHaveValue('Tool enabled')
  expect(screen.getByRole('button', { name: 'Remove factor' })).toBeEnabled()
})

it('only offers individual factors for disconnected tools', () => {
  render(<ToolFactorDialog toolNodeId="tool" graph={{ ...graph, edges: [] }} factors={[]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.getByRole('button', { name: 'This tool on/off' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Tool levels' }))
  expect(screen.getByRole('status')).toHaveTextContent('Connect this Tool to an Agent first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it.each(['This tool on/off', 'Tool levels', 'Tools allowed'])('blocks %s when an Agent already has an all-tools factor', (mode) => {
  const grouped = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'Writer', factor_bindings: { tool_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { server_id: 'a' } } },
    ],
  } as ProtocolGraph
  render(<ToolFactorDialog toolNodeId="tool" graph={grouped} factors={[{ name: 'Group', level_type: 'tool_toggle', levels: [['tool'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  fireEvent.click(screen.getByRole('button', { name: mode }))
  expect(screen.getByRole('status')).toHaveTextContent('Group')
  expect(screen.getByRole('status')).toHaveTextContent('remove it first')
  expect(screen.queryByLabelText('Factor name')).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Save factor' })).toBeDisabled()
})

it('edits allow-list levels while keeping the server pinned', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  vi.spyOn(mcpServersApi, 'list').mockResolvedValue([{ id: 'a', capabilities: { tools: [{ name: 'search' }, { name: 'fetch' }] } }] as Awaited<ReturnType<typeof mcpServersApi.list>>)
  const save = vi.fn().mockResolvedValue(undefined)
  const unbound = { ...graph, nodes: graph.nodes.map((node) => node.id === 'tool' ? { ...node, data: { label: 'Search server', config: { server_id: 'a', tool_names: ['search'] } } } : node) } as ProtocolGraph
  render(<QueryClientProvider client={client}><ToolFactorDialog toolNodeId="tool" graph={unbound} factors={[]} initialMode="tool_names" onClose={vi.fn()} onSave={save} onRemove={vi.fn()} /></QueryClientProvider>)
  await screen.findAllByText('fetch')
  fireEvent.click(screen.getAllByRole('switch')[1])
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith(expect.objectContaining({ level_type: 'tool_names', levels: [['search', 'fetch'], []] }), 'tool', undefined))
  expect((unbound.nodes[1].data.config as { server_id: string }).server_id).toBe('a')
  client.clear()
})

it('saves edits to an existing individual factor without removing other declarations', async () => {
  const save = vi.fn().mockResolvedValue(undefined)
  render(<ToolFactorDialog toolNodeId="tool" graph={graph} factors={[{ name: 'Tool enabled', level_type: 'boolean', levels: [false, true] }]} onClose={vi.fn()} onSave={save} onRemove={vi.fn()} />)
  fireEvent.change(screen.getByLabelText('Factor name'), { target: { value: 'Renamed' } })
  fireEvent.click(screen.getByRole('button', { name: 'Save factor' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith({ name: 'Renamed', level_type: 'boolean', levels: [false, true], level_labels: ['Disabled', 'Enabled'] }, 'tool', 'Tool enabled'))
})

it('opens the existing group factor when a shared tool has multiple connected agents', () => {
  const sharedGraph = {
    ...graph,
    nodes: [
      { ...graph.nodes[0], data: { label: 'First agent' } },
      { id: 'second', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Second agent', tool_selection: ['a'], tool_factor_mode: 'tool_toggle', factor_bindings: { tool_selection: 'Group' } } },
      { ...graph.nodes[1], data: { label: 'Summarize', config: { server_id: 'a' } } },
    ],
    edges: [...graph.edges, { id: 'second-edge', source: 'tool', target: 'second', targetHandle: 'tool' }],
  } as ProtocolGraph
  render(<ToolFactorDialog toolNodeId="tool" graph={sharedGraph} factors={[{ name: 'Group', level_type: 'tool_toggle', levels: [['tool'], []], level_labels: ['All enabled', 'All disabled'] }]} onClose={vi.fn()} onSave={vi.fn()} onRemove={vi.fn()} />)
  expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
  expect(screen.getByText('Applies to: First agent, Second agent')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'All tools on/off' })).toHaveAttribute('aria-pressed', 'true')
  expect(screen.getByLabelText('Factor name')).toHaveValue('Group')
})
