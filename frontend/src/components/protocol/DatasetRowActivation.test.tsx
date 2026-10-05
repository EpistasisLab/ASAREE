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
