import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { Experiment, ExperimentRunResults } from '@/types/experiments'
import { ResultsInspectorPanel, ResultsTab } from './ResultsTab'
afterEach(() => vi.restoreAllMocks())
vi.mock('./NodeRunOutputPanel', () => ({ RunStepTrace: ({ runId }: { runId: string }) => <p>Trace {runId}</p>, ReceivedPromptPanel: () => null, UnresolvedReferencesNote: () => null }))
it('preserves failed attempt output while inspecting the latest row', async () => {
 const row = { row_result_id: 'slot', replicate_number: 1, dataset_row: { dataset_id: 'dataset-uuid', raw_sha256: 'original-hash', row_index: 2 }, design_revision_id: 'design', protocol_revision_id: 'publication', latest_attempt: { run_id: 'B' }, attempts: [{ run_id: 'A', status: 'failed', current: false, error: 'old failure', node_runs: { agent: { run_id: 'trace-A', output_text: 'old output' } }, attempt_result: {} }, { run_id: 'B', status: 'completed', current: true, node_runs: { agent: { run_id: 'trace-B', output_text: 'new output' } }, attempt_result: {} }] }
 vi.spyOn(experimentsApi, 'getRunResults').mockResolvedValue({ row_results: [row] } as unknown as ExperimentRunResults)
 const scope = { protocol_id: 'p', design_revision_id: 'design', protocol_revision_id: 'publication' }
 render(<QueryClientProvider client={new QueryClient()}><ResultsInspectorPanel experimentId="e" experiment={{} as Experiment} selection={{ type: 'row', rowResultId: 'slot', scope }} onClose={vi.fn()} /></QueryClientProvider>)
 await screen.findByText('new output')
 expect(screen.getByText('original-hash')).toBeInTheDocument()
 fireEvent.change(screen.getByRole('combobox', { name: 'Inspect attempt' }), { target: { value: 'A' } })
 expect(screen.getByText('old output')).toBeInTheDocument()
 expect(screen.getByRole('alert')).toHaveTextContent('old failure')
 expect(screen.getByText('Trace trace-A')).toBeInTheDocument()
 expect(experimentsApi.getRunResults).toHaveBeenCalledWith('e', scope)
})

it('a selected experiment version requests one publication scope without independent design controls', async () => {
 const results = vi.spyOn(experimentsApi, 'getRunResults').mockResolvedValue({ consumption_mode: 'per_row', row_results: [], row_summary: null } as unknown as ExperimentRunResults)
 render(<QueryClientProvider client={new QueryClient()}><ResultsTab protocolId="p" versionId="publication-old" experimentId="e" experimentName="Rows" experiment={{} as Experiment} onSelectResult={vi.fn()} /></QueryClientProvider>)
 await waitFor(() => expect(results).toHaveBeenLastCalledWith('e', { protocol_id: 'p', protocol_revision_id: 'publication-old' }))
 expect(screen.queryByRole('combobox', { name: 'Results design revision' })).not.toBeInTheDocument()
})
