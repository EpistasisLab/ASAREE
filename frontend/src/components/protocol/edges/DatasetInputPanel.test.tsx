import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeEach, expect, it, vi } from 'vitest'
import { datasetsApi } from '@/api/client'
import { DatasetInputPanel } from './DatasetInputPanel'

const state = vi.hoisted(() => ({ nodes: [{ id: 'd', type: 'dataset', data: { config: { dataset_id: '11111111-1111-4111-8111-111111111111' } } }, { id: 'a', type: 'agent', data: { config: {} } }], edges: [] as Array<Record<string, any>>, setEdges: vi.fn() }))
vi.mock('@xyflow/react', () => ({ useNodes: () => state.nodes, useEdges: () => state.edges, useReactFlow: () => ({ setEdges: state.setEdges }) }))
vi.mock('@/api/client', () => ({ datasetsApi: { getRowSchema: vi.fn() } }))
beforeEach(() => {
  state.setEdges.mockClear()
  state.edges = [{ id: 'edge', source: 'd', target: 'a', targetHandle: 'dataset', data: { handoff: { mode: 'full' }, custom: 'keep' } }]
  state.setEdges.mockImplementation(fn => { state.edges = fn(state.edges) })
  vi.mocked(datasetsApi.getRowSchema).mockResolvedValue({ dataset_id: 'id', raw_sha256: 'hash', row_count: 3, columns: ['question', 'reference'] })
})
function mount() { return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><DatasetInputPanel edgeId="edge" source="d" /></QueryClientProvider>) }
it('defaults to whole, requires explicit columns, preserves metadata and CSV order', async () => {
  mount()
  expect(screen.getByLabelText('Whole dataset')).toBeChecked()
  await screen.findByText(/3 original rows/)
  fireEvent.click(screen.getByLabelText('Per row'))
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
  fireEvent.click(screen.getByRole('checkbox', { name: 'reference' }))
  fireEvent.click(screen.getByRole('checkbox', { name: 'question' }))
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(state.edges[0].data).toEqual({ custom: 'keep', handoff: { mode: 'full' }, dataset_input: { mode: 'per_row', columns: ['question', 'reference'] } })
  fireEvent.click(screen.getByLabelText('Whole dataset'))
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(state.edges[0].data).toEqual({ custom: 'keep', handoff: { mode: 'full' } })
})
it('keeps unknown saved columns visible and blocks apply', async () => {
  state.edges[0].data.dataset_input = { mode: 'per_row', columns: ['missing'] }
  mount()
  await screen.findByText('Saved columns are missing from the original CSV.')
  expect(screen.getByRole('checkbox', { name: 'missing' })).toBeChecked()
  expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
})
it('reports source errors without saving drafts', async () => {
  vi.mocked(datasetsApi.getRowSchema).mockRejectedValue(new Error('Source unavailable'))
  mount()
  await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Source unavailable'))
  expect(state.setEdges).not.toHaveBeenCalled()
})
it('acknowledges Apply and explains that production needs publication in both modes', async () => {
  mount()
  await screen.findByText(/3 original rows/)
  fireEvent.click(screen.getByLabelText('Per row'))
  fireEvent.click(screen.getByRole('checkbox', { name: 'question' }))
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(screen.getByRole('status')).toHaveTextContent('Per row applied to the draft')
  expect(screen.getByRole('status')).toHaveTextContent('Publish the canvas to update production runs and results')
  fireEvent.click(screen.getByLabelText('Whole dataset'))
  expect(screen.queryByRole('status')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
  expect(screen.getByRole('status')).toHaveTextContent('Whole dataset applied to the draft')
})
