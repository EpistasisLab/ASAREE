import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { protocolsApi } from '@/api/client'
import type { ExperimentRunResults } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { DatasetRowRuns } from './DatasetRowRuns'

afterEach(() => vi.restoreAllMocks())
it('retries only the failed row and keeps completed unscored disabled', async () => {
 const retry = vi.spyOn(protocolsApi, 'runCells').mockRejectedValue(new Error('invalid_retry_target'))
 const results = { row_summary: { cell_count: 1, parent_replicate_count: 1, row_count: 2, expected: 2 }, row_results: ['failed','completed'].map((status,index) => ({ row_result_id: `slot-${index}`, cell_label: 'same', dataset_row: { row_index: index }, replicate_number: 1, status, protocol_revision_id: 'r', latest_attempt: { run_id: `run-${index}` } })) } as unknown as ExperimentRunResults
 const protocol = { id: 'p', graph: { nodes: [], edges: [] }, published_revision_id: 'r' } as unknown as Protocol
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={results} protocol={protocol} blocked={false} /></QueryClientProvider>)
 const buttons = screen.getAllByRole('button', { name: 'Retry' })
 expect(buttons[1]).toBeDisabled()
 fireEvent.click(buttons[0])
 await waitFor(() => expect(retry).toHaveBeenCalledWith('p', { retry_row_result_ids: ['slot-0'] }))
 expect(await screen.findByRole('alert')).toHaveTextContent('invalid_retry_target')
})
