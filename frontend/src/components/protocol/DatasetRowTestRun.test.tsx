import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { fireEvent, render, renderHook, screen, waitFor, act } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { datasetsApi, protocolsApi } from '@/api/client'
import { defaultAgentNodeData, defaultAnthropicModelNodeData, defaultDatasetNodeData, defaultReasonActPatternNodeData, type ProtocolGraph } from '@/types/protocols'
import { ProtocolCanvas } from './ProtocolCanvas'
import { useDatasetRowSelection } from './useDatasetRowSelection'

vi.mock('./PythonCodeEditor', () => ({ PythonCodeEditor: () => null }))
const graph: ProtocolGraph = { nodes: [
 { id: 'a', type: 'agent', position: { x: 100, y: 100 }, data: defaultAgentNodeData('Prediction') },
 { id: 'm', type: 'model_anthropic', position: { x: 0, y: 0 }, data: defaultAnthropicModelNodeData() },
 { id: 'd', type: 'dataset', position: { x: 0, y: 200 }, data: { ...defaultDatasetNodeData(), config: { ...defaultDatasetNodeData().config, dataset_id: '11111111-1111-4111-8111-111111111111', dataset_name: 'original' } } },
 { id: 'pattern', type: 'reason_act', position: { x: 0, y: 300 }, data: defaultReasonActPatternNodeData() },
 ], edges: [ { id: 'model', source: 'm', target: 'a', targetHandle: 'model' }, { id: 'pattern', source: 'pattern', target: 'a', targetHandle: 'architectural_pattern' }, { id: 'dataset', source: 'd', target: 'a', targetHandle: 'dataset', data: { dataset_input: { mode: 'per_row', columns: ['question'] } } } ] }
function setup() {
 vi.stubGlobal('DOMMatrixReadOnly', class { m22 = 1 })
 vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, top: 0, left: 0, right: 1000, bottom: 800, width: 1000, height: 800, toJSON: () => ({}) })
 vi.stubGlobal('ResizeObserver', class { private callback: ResizeObserverCallback; constructor(callback: ResizeObserverCallback) { this.callback = callback } observe(target: Element) { this.callback([{ target, contentRect: target.getBoundingClientRect() } as ResizeObserverEntry], this as unknown as ResizeObserver) } unobserve() {} disconnect() {} })
 vi.spyOn(protocolsApi, 'get').mockResolvedValue({ id: 'p', published_revision_id: 'r' } as never)
 vi.spyOn(protocolsApi, 'getRevision').mockResolvedValue({ id: 'r', graph } as never)
 vi.spyOn(datasetsApi, 'getRowSchema').mockResolvedValue({ dataset_id: 'd', raw_sha256: 'hash', row_count: 3, columns: ['question','reference'] })
 vi.spyOn(protocolsApi, 'listRuns').mockResolvedValue([])
 vi.spyOn(protocolsApi, 'getLatestTestRun').mockRejectedValue(new Error('No test'))
 vi.spyOn(protocolsApi, 'update').mockResolvedValue({} as never)
}
beforeEach(setup)
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })
function mount() {
 render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter><ReactFlowProvider><div style={{ width: 1000, height: 800 }}><ProtocolCanvas protocolId="p" experimentId={null} initialGraph={graph} hasUnpublishedChanges={false} publishedRevision={1} /></div></ReactFlowProvider></MemoryRouter></QueryClientProvider>)
}
it('canvas Test Run selects published source row 3 and submits only index 2', async () => {
 const start = vi.spyOn(protocolsApi, 'testRun').mockRejectedValue(new Error('mock queue'))
 mount()
 fireEvent.click(await screen.findByRole('button', { name: /Test Run/i }))
 const selector = await screen.findByRole('spinbutton', { name: 'Source row' })
 await waitFor(() => expect(selector).not.toBeDisabled())
 expect(selector).toHaveValue(1)
 fireEvent.change(selector, { target: { value: '3' } })
 expect(selector).toHaveValue(3)
 expect(start).not.toHaveBeenCalled()
 const buttons = screen.getAllByRole('button', { name: 'Test Run' })
 fireEvent.click(buttons.at(-1)!)
 await waitFor(() => expect(start).toHaveBeenCalledWith('p', { row_index: 2 }))
})
it('source row control validates bounds and converts one-based display', async () => {
 const { DatasetRowSelector } = await import('./DatasetRowSelector')
 const change = vi.fn()
 render(<DatasetRowSelector rowIndex={0} rowCount={3} onChange={change} />)
 fireEvent.change(screen.getByRole('spinbutton'), { target: { value: '3' } })
 expect(change).toHaveBeenCalledWith(2)
})

it('published selection resets after publication changes and rejects invalid bounds and columns', async () => {
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
 const wrapper = ({ children }: { children: React.ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>
 const { result, rerender } = renderHook(({ scope, source }) => useDatasetRowSelection(source, scope), { wrapper, initialProps: { scope: 'publication-1', source: graph } })
 await waitFor(() => expect(result.current.error).toBeNull())
 act(() => result.current.setRowIndex(2))
 expect(result.current.rowIndex).toBe(2)
 rerender({ scope: 'publication-2', source: graph })
 await waitFor(() => expect(result.current.error).toBeNull())
 expect(result.current.rowIndex).toBe(0)
 act(() => result.current.setRowIndex(3))
 expect(result.current.error).toMatch(/outside/)
 const invalid = structuredClone(graph)
 invalid.edges[2].data = { dataset_input: { mode: 'per_row', columns: ['missing'] } }
 act(() => result.current.setRowIndex(0))
 rerender({ scope: 'publication-2', source: invalid })
 expect(result.current.error).toMatch(/columns are absent/)
 rerender({ scope: 'whole', source: { ...graph, edges: graph.edges.filter(edge => edge.id !== 'dataset') } })
 expect(result.current.binding).toBeUndefined()
 expect(result.current.error).toBeNull()
})
