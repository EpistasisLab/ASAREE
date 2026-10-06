import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { datasetsApi, protocolsApi } from '@/api/client'
import { defaultAgentNodeData, defaultDatasetNodeData, type ProtocolGraph } from '@/types/protocols'
import { PromptPreviewPanel } from './PromptPreviewPanel'

afterEach(() => vi.restoreAllMocks())
it('draft preview selection requests only text with graph and index', async () => {
 const graph: ProtocolGraph = { nodes: [{ id: 'agent', type: 'agent', position: { x: 0, y: 0 }, data: defaultAgentNodeData() }, { id: 'dataset', type: 'dataset', position: { x: 0, y: 0 }, data: { ...defaultDatasetNodeData(), config: { ...defaultDatasetNodeData().config, dataset_id: '11111111-1111-4111-8111-111111111111' } } }], edges: [{ id: 'e', source: 'dataset', target: 'agent', targetHandle: 'dataset', data: { dataset_input: { mode: 'per_row', columns: ['question'] } } }] }
 vi.spyOn(datasetsApi, 'getRowSchema').mockResolvedValue({ dataset_id: 'draft', raw_sha256: 'draft-hash', columns: ['question'], row_count: 3 })
 const preview = vi.spyOn(protocolsApi, 'promptPreview').mockImplementation(async (_id, _node, _graph, selection) => ({ text: 'Authorized question', dataset_row: { dataset_id: 'draft', raw_sha256: 'draft-hash', row_index: selection?.row_index ?? 0, columns: ['question'], values: { question: 'Authorized question' } } }))
 const run = vi.spyOn(protocolsApi, 'testRun')
 render(<QueryClientProvider client={new QueryClient()}><PromptPreviewPanel graph={graph} nodeId="agent" signature={JSON.stringify(graph)} fetchPreview={rowIndex => protocolsApi.promptPreview('p', 'agent', graph, { row_index: rowIndex })} /></QueryClientProvider>)
 fireEvent.click(screen.getByRole('button', { name: 'Prompt this agent will receive' }))
 await screen.findByText('Authorized question')
 expect(preview).toHaveBeenCalledWith('p', 'agent', graph, { row_index: 0 })
 fireEvent.change(screen.getByRole('spinbutton', { name: 'Source row' }), { target: { value: '3' } })
 await waitFor(() => expect(preview).toHaveBeenLastCalledWith('p', 'agent', graph, { row_index: 2 }))
 await screen.findByText(/Source row 3 · draft · draft-hash/)
 expect(run).not.toHaveBeenCalled()
})
