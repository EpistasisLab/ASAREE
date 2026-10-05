import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { ExperimentRunResults } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunsTab } from './RunsTab'

export const rowResults = {
 consumption_mode: 'per_row', cells: [], replicates: [], overview: {},
 row_summary: { cell_count: 5, parent_replicate_count: 10, row_count: 3, expected: 30, planned: 30, pending: 1, running: 2, completed: 26, failed: 1, cancelled: 0, scored: 25, missing_reported: 1, metric_coverage: {} },
 row_results: ['failed', 'completed', 'finalizing'].map((status, index) => ({ row_result_id: `slot-${index}`, cell_label: 'duplicate', replicate_label: 'same', replicate_number: 1, dataset_row: { row_index: index, dataset_id: 'd', raw_sha256: 'hash' }, status, protocol_revision_id: 'r', latest_attempt: { run_id: `run-${index}` }, attempts: [] })),
} as unknown as ExperimentRunResults
export const protocol = { name: 'Rows', description: null, created_at: '', updated_at: '', id: 'p', experiment_id: 'e', published_revision_id: 'r', published_revision: 1, has_unpublished_changes: false, graph: { nodes: [], edges: [] } } as Protocol
afterEach(() => vi.restoreAllMocks())
export function mountRows(inspect = vi.fn(), results = rowResults) {
 vi.spyOn(experimentsApi, 'listReplicates').mockResolvedValue([])
 vi.spyOn(experimentsApi, 'listTrials').mockResolvedValue([])
 vi.spyOn(experimentsApi, 'getRunResults').mockResolvedValue(results)
 const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
 render(<QueryClientProvider client={client}><RunsTab experimentId="e" designSpec={{}} protocol={protocol} regenerationRequired={false} unboundFactors={[]} onViewResult={vi.fn()} onViewRowResult={inspect} /></QueryClientProvider>)
 return client
}
it('renders authoritative forecast and distinct row identities', async () => {
 const inspect = vi.fn()
 mountRows(inspect)
 expect(await screen.findByText('5 cells / 10 replicates / 3 rows / 30 executions')).toBeInTheDocument()
 expect(screen.getByText(/26 completed.*1 failed.*1 missing reported/)).toBeInTheDocument()
 fireEvent.click(screen.getAllByRole('button', { name: 'Output' })[1])
 expect(inspect).toHaveBeenCalledWith('slot-1')
 expect(experimentsApi.getRunResults).toHaveBeenCalledWith('e', { protocol_id: 'p' })
})
it('cancels exactly the active row run', async () => {
 const cancel = vi.spyOn(protocolsApi, 'cancelRun').mockResolvedValue({} as never)
 mountRows()
 fireEvent.click(await screen.findByRole('button', { name: 'Cancel' }))
 await waitFor(() => expect(cancel).toHaveBeenCalledWith('p', 'run-2'))
})
