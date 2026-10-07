import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { ExperimentRunResults } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunsTab } from './RunsTab'
import { DatasetRowRuns } from './DatasetRowRuns'

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
it('runs an unstarted source row in one selected replicate', async () => {
 const run = vi.spyOn(protocolsApi, 'runCells').mockResolvedValue({} as never)
 const results = { ...rowResults, row_results: [], row_cells: [{ cell_id: 'a', cell_label: 'short', factor_values: {}, replicate_count: 2, replicates: [
  { replicate_result_id: 'parent', replicate_label: 'short', replicate_number: 1 },
  { replicate_result_id: 'parent-2', replicate_label: 'short__rep2', replicate_number: 2 },
 ] }] }
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={results} protocol={protocol} blocked={false} /></QueryClientProvider>)
 const sourceRows = screen.getAllByRole('row')
 expect(sourceRows).toHaveLength(7)
 fireEvent.click(within(sourceRows[2]).getByRole('button', { name: 'Run' }))
 expect(run).not.toHaveBeenCalled()
 expect(screen.getByText('Run source row 2?')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button', { name: 'Run row' }))
 await waitFor(() => expect(run).toHaveBeenCalledWith('p', { replicateLabels: ['short'], rerunReplicateLabels: [], row_indices: [1] }))
})

it('reruns only a finished source row and leaves active rows with stop controls', async () => {
 const run = vi.spyOn(protocolsApi, 'runCells').mockResolvedValue({} as never)
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={rowResults} protocol={protocol} blocked={false} /></QueryClientProvider>)
 expect(screen.getAllByRole('button', { name: 'Re-run' })).toHaveLength(1)
 expect(screen.getAllByRole('button', { name: 'Stop' })).toHaveLength(1)
 fireEvent.click(screen.getByRole('button', { name: 'Re-run' }))
 fireEvent.click(screen.getByRole('button', { name: 'Re-run row' }))
 await waitFor(() => expect(run).toHaveBeenCalledWith('p', { replicateLabels: ['same'], rerunReplicateLabels: ['same'], row_indices: [1] }))
})

it('renders authoritative forecast and distinct row identities', async () => {
 const inspect = vi.fn()
 mountRows(inspect)
 expect(await screen.findByText('5 cells / 10 replicates / 3 rows / 30 executions')).toBeInTheDocument()
 expect(screen.getByText(/26 completed.*1 failed.*1 missing reported/)).toBeInTheDocument()
 fireEvent.click(screen.getAllByRole('button', { name: 'View results' })[1])
 expect(inspect).toHaveBeenCalledWith('slot-1')
 expect(experimentsApi.getRunResults).toHaveBeenCalledWith('e', { protocol_id: 'p' })
})
it('cancels exactly the active row run', async () => {
 const cancel = vi.spyOn(protocolsApi, 'cancelRun').mockResolvedValue({} as never)
 mountRows()
 fireEvent.click(await screen.findByRole('button', { name: 'Stop' }))
 await waitFor(() => expect(cancel).toHaveBeenCalledWith('p', 'run-2'))
})

it('shows generated cells before row executions are planned', () => {
 const results = { ...rowResults, row_results: [], row_cells: [
  { cell_id: 'cell-a', cell_label: 'Prompt:short', factor_values: { Prompt: 'short' }, replicate_count: 2, replicates: [
   { replicate_result_id: 'parent-1', replicate_label: 'short', replicate_number: 1 },
   { replicate_result_id: 'parent-2', replicate_label: 'short__rep2', replicate_number: 2 },
  ] },
  { cell_id: 'cell-b', cell_label: 'Prompt:long', factor_values: { Prompt: 'long' }, replicate_count: 2 },
 ] }
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={results} protocol={protocol} blocked={false} /></QueryClientProvider>)
 expect(screen.getByRole('button', { name: 'View cell Prompt:short' })).toBeInTheDocument()
 expect(screen.getByRole('button', { name: 'View cell Prompt:long' })).toBeInTheDocument()
 expect(screen.getAllByText('0/6')).toHaveLength(2)
 fireEvent.click(screen.getByRole('button', { name: 'View cell Prompt:short' }))
 expect(screen.getByText('Replicate 1')).toBeInTheDocument()
 expect(screen.getByText('Replicate 2')).toBeInTheDocument()
 expect(screen.getAllByRole('button', { name: 'View results' }).every(button => button.hasAttribute('disabled'))).toBe(true)
 expect(screen.getAllByRole('button', { name: 'View rows' })).toHaveLength(2)
 expect(screen.queryByRole('button', { name: 'View Prompt:short replicate 1' })).not.toBeInTheDocument()
 expect(screen.getByRole('button', { name: 'Run Prompt:short replicate 1' })).toBeEnabled()
 expect(screen.getByRole('button', { name: 'Run all replicates in Prompt:short' })).toBeEnabled()
})

it('runs only the selected replicate across source rows after confirmation', async () => {
 const run = vi.spyOn(protocolsApi, 'runCells').mockResolvedValue({} as never)
 const results = { ...rowResults, row_results: [], row_cells: [{ cell_id: 'a', cell_label: 'short', factor_values: {}, replicate_count: 1, replicates: [{ replicate_result_id: 'parent', replicate_label: 'short', replicate_number: 1 }] }] }
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={results} protocol={protocol} blocked={false} /></QueryClientProvider>)
 fireEvent.click(screen.getByRole('button', { name: 'View cell short' }))
 fireEvent.click(screen.getByRole('button', { name: 'Run short replicate 1' }))
 expect(run).not.toHaveBeenCalled()
 expect(screen.getByText('1 replicates × 3 rows = 3 executions')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button', { name: 'Run selected replicates' }))
 await waitFor(() => expect(run).toHaveBeenCalledWith('p', { replicateLabels: ['short'], rerunReplicateLabels: [] }))
})

it('keeps multiple cells expanded and explicitly reruns completed rows', async () => {
 const run = vi.spyOn(protocolsApi, 'runCells').mockResolvedValue({} as never)
 const cells = ['a', 'b'].map(id => ({ cell_id: id, cell_label: id, factor_values: {}, replicate_count: 1, replicates: [{ replicate_result_id: `parent-${id}`, replicate_label: id, replicate_number: 1 }] }))
 const results: ExperimentRunResults = { ...rowResults, row_cells: cells, row_results: rowResults.row_results!.map((row, index) => ({ ...row, cell_id: 'a', replicate_result_id: 'parent-a', replicate_label: 'a', status: index === 2 ? 'running' : 'completed' })) }
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={results} protocol={protocol} blocked={false} /></QueryClientProvider>)
 fireEvent.click(screen.getByRole('button', { name: 'View cell a' }))
 fireEvent.click(screen.getByRole('button', { name: 'View cell b' }))
 expect(screen.getByRole('list', { name: 'Replicates for a' })).toBeInTheDocument()
 expect(screen.getByRole('list', { name: 'Replicates for b' })).toBeInTheDocument()
 expect(screen.getByRole('button', { name: 'Run a replicate 1' })).toBeDisabled()
 fireEvent.click(screen.getByRole('button', { name: 'Run all replicates in a' }))
 expect(screen.getByText(/2 executions will start/)).toBeInTheDocument()
 expect(screen.getByText(/previous attempts remain available/)).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button', { name: 'Run selected replicates' }))
 await waitFor(() => expect(run).toHaveBeenCalledWith('p', { replicateLabels: ['a'], rerunReplicateLabels: ['a'] }))
})

it('stops all active row runs without stopping finished rows', async () => {
 const cancel = vi.spyOn(protocolsApi, 'cancelRun').mockResolvedValue({} as never)
 const results: ExperimentRunResults = { ...rowResults, row_cells: [{ cell_id: 'a', cell_label: 'a', factor_values: {}, replicate_count: 1 }], row_results: rowResults.row_results!.map((row, index) => ({ ...row, cell_id: 'a', status: index === 0 ? 'completed' : 'running' })) }
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowRuns results={results} protocol={protocol} blocked={false} /></QueryClientProvider>)
 fireEvent.click(screen.getAllByRole('button', { name: 'Stop all' })[0])
 await waitFor(() => expect(cancel).toHaveBeenCalledTimes(2))
 expect(cancel).toHaveBeenCalledWith('p', 'run-1')
 expect(cancel).toHaveBeenCalledWith('p', 'run-2')
})
