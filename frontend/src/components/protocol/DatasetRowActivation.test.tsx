import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { datasetsApi } from '@/api/client'
import { toPersistedGraph } from '@/lib/protocolGraph'
import { Position } from '@xyflow/react'
import { InteractEdge } from './edges/InteractEdge'

const state = vi.hoisted(() => ({ edges: [] as any[], nodes: [] as any[], setEdges: vi.fn() }))
vi.mock('@xyflow/react', async importOriginal => ({ ...await importOriginal<typeof import('@xyflow/react')>(), BaseEdge: () => null, EdgeLabelRenderer: ({ children }: any) => children, EdgeToolbar: ({ children }: any) => children, useNodes: () => state.nodes, useEdges: () => state.edges, useReactFlow: () => ({ getNode: (id: string) => state.nodes.find(node => node.id === id), setEdges: state.setEdges }) }))
vi.mock('./ProtocolCanvasContext', () => ({ useProtocolCanvasActions: () => ({ requestEdgeInsert: vi.fn(), experimentLocked: false }) }))
beforeEach(() => {
 state.nodes = [{ id: 'd', type: 'dataset', position: { x: 0, y: 0 }, data: { config: { dataset_id: '11111111-1111-4111-8111-111111111111' } } }, { id: 'a', type: 'agent', position: { x: 0, y: 0 }, data: { config: {} } }]
 state.edges = [{ id: 'edge', source: 'd', target: 'a', targetHandle: 'dataset', data: { custom: 'keep' } }]
 state.setEdges.mockImplementation(fn => { state.edges = fn(state.edges) })
 vi.spyOn(datasetsApi, 'getRowSchema').mockResolvedValue({ dataset_id: 'id', raw_sha256: 'hash', row_count: 3, columns: ['question','reference'] })
})
function mount(handle = 'dataset') {
 render(<QueryClientProvider client={new QueryClient()}><svg><InteractEdge id="edge" source="d" target="a" sourceX={0} sourceY={0} targetX={100} targetY={100} sourcePosition={Position.Right} targetPosition={Position.Left} targetHandleId={handle} data={{ custom: 'keep' }} /></svg></QueryClientProvider>)
}
it('Dataset edge settings apply through production serialization and reload', async () => {
 mount()
 fireEvent.click(screen.getByRole('button', { name: 'Dataset input settings' }))
 await screen.findByText(/3 original rows/)
 expect(screen.getByLabelText('Whole dataset')).toBeChecked()
 fireEvent.click(screen.getByLabelText('Per row'))
 expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled()
 fireEvent.click(screen.getByRole('checkbox', { name: 'question' }))
 fireEvent.click(screen.getByRole('button', { name: 'Apply' }))
 const graph = toPersistedGraph(state.nodes, state.edges)
 expect(JSON.parse(JSON.stringify(graph)).edges[0].data).toEqual({ custom: 'keep', dataset_input: { mode: 'per_row', columns: ['question'] } })
})
it('unrelated typed edge has no Dataset settings', () => {
 mount('model')
 expect(screen.queryByRole('button', { name: 'Dataset input settings' })).not.toBeInTheDocument()
})
it.each([true, false])('only disables Per row when the existing driver is a different dataset (same: %s)', async sameDataset => {
 state.nodes.push({ id: 'other-dataset', type: 'dataset', position: { x: 0, y: 0 }, data: { config: { dataset_id: sameDataset ? '11111111-1111-4111-8111-111111111111' : '22222222-2222-4222-8222-222222222222' } } })
 state.edges.push({ id: 'other-edge', source: 'other-dataset', target: 'a', targetHandle: 'dataset', data: { dataset_input: { mode: 'per_row', columns: ['question'] } } })
 mount()
 fireEvent.click(screen.getByRole('button', { name: 'Dataset input settings' }))
 await screen.findByText(/3 original rows/)
 expect(screen.getByLabelText('Whole dataset')).toBeEnabled()
 if (sameDataset) {
  expect(screen.getByLabelText('Per row')).toBeEnabled()
 } else {
  expect(screen.getByLabelText('Per row')).toBeDisabled()
  expect(screen.getByText(/Per row is unavailable because another dataset already uses it/)).toBeInTheDocument()
 }
})
it.each([10, 11])('selects and applies %i columns inline or in a modal', async count => {
 const columns = Array.from({ length: count }, (_, index) => `column_${index + 1}`)
 vi.mocked(datasetsApi.getRowSchema).mockResolvedValue({ dataset_id: 'id', raw_sha256: 'hash', row_count: 3, columns })
 mount()
 fireEvent.click(screen.getByRole('button', { name: 'Dataset input settings' }))
 await screen.findByText(/3 original rows/)
 fireEvent.click(screen.getByLabelText('Per row'))
 if (count > 10) {
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Select columns' }))
  await screen.findByRole('dialog', { name: 'Select dataset columns' })
 } else {
  expect(screen.queryByRole('button', { name: 'Select columns' })).not.toBeInTheDocument()
 }
 expect(screen.getAllByRole('checkbox')).toHaveLength(count)
 fireEvent.click(screen.getByRole('button', { name: 'All' }))
 screen.getAllByRole('checkbox').forEach(checkbox => expect(checkbox).toBeChecked())
 fireEvent.click(screen.getByRole('button', { name: 'None' }))
 screen.getAllByRole('checkbox').forEach(checkbox => expect(checkbox).not.toBeChecked())
 fireEvent.click(screen.getByRole('checkbox', { name: 'column_2' }))
 if (count > 10) {
  fireEvent.click(screen.getByRole('button', { name: 'Done' }))
  await screen.findByRole('button', { name: 'Apply' })
  expect(screen.getByText(`1 of ${count} columns selected`)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Select columns' }))
  expect(await screen.findByRole('checkbox', { name: 'column_2' })).toBeChecked()
  fireEvent.click(screen.getByRole('button', { name: 'Done' }))
 }
 fireEvent.click(await screen.findByRole('button', { name: 'Apply' }))
 expect(toPersistedGraph(state.nodes, state.edges).edges[0].data).toEqual({ custom: 'keep', dataset_input: { mode: 'per_row', columns: ['column_2'] } })
})
