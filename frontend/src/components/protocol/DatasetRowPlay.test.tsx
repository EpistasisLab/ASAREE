import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ReactFlowProvider } from '@xyflow/react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { datasetsApi, protocolsApi } from '@/api/client'
import { defaultAgentNodeData, defaultAnthropicModelNodeData, defaultDatasetNodeData, defaultReasonActPatternNodeData, type ProtocolGraph } from '@/types/protocols'
import { ProtocolCanvas } from './ProtocolCanvas'

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
it('node hover Play pins selected row and submits exactly one node request', async () => {
 const play = vi.spyOn(protocolsApi, 'runNode').mockRejectedValue(new Error('mock queue'))
 const testRun = vi.spyOn(protocolsApi, 'testRun')
 mount()
 const button = document.querySelector<HTMLButtonElement>('button[aria-label^="Run this node"]')
 expect(button).not.toBeNull()
 fireEvent.click(button!)
 const selector = await screen.findByRole('spinbutton', { name: 'Source row' })
 await waitFor(() => expect(selector).not.toBeDisabled())
 fireEvent.change(selector, { target: { value: '3' } })
 fireEvent.click(screen.getByRole('button', { name: /Run anyway|Confirm & run/ }))
 await waitFor(() => expect(play).toHaveBeenCalledWith('p', 'a', { row_index: 2 }))
 expect(testRun).not.toHaveBeenCalled()
})
