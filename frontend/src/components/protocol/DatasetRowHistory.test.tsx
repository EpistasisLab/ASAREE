import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { ProtocolRevision } from '@/types/protocols'
import type { Experiment, ExperimentRunResults } from '@/types/experiments'
import { ResultsInspectorPanel, ResultsTab } from './ResultsTab'
import { DatasetRowDetail } from './DatasetRowResults'
import type { RowResult } from '@/types/experiments'
afterEach(() => vi.restoreAllMocks())
vi.mock('./NodeRunOutputPanel', () => ({ RunStepTrace: ({ runId }: { runId: string }) => <p>Trace {runId}</p>, ReceivedPromptPanel: () => null, UnresolvedReferencesNote: () => null }))
it('hides empty configuration nodes but preserves output, traces and execution failures', () => {
 const attempt = { run_id: 'run', status: 'completed', node_labels: {
  model: 'Model config', dataset: 'Dataset config', skipped: 'Skipped node', answer: 'Answer agent',
  trace: 'Trace agent', failed: 'Failed agent', active: 'Active agent', cancelled: 'Cancelled agent',
 }, node_runs: {
  model: { status: 'completed', output_text: null, error: null },
  dataset: { status: 'completed', output_text: '' },
  skipped: { status: 'skipped' },
  answer: { status: 'completed', output_text: 'Final answer' },
  trace: { status: 'completed', run_id: 'agent-run' },
  failed: { status: 'failed', error: 'Execution failed' },
  active: { status: 'running' },
  cancelled: { status: 'cancelled' },
 } }
 const row = { row_result_id: 'slot', replicate_number: 1, dataset_row: { row_index: 0 }, latest_attempt: attempt, attempts: [attempt] } as unknown as RowResult
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowDetail row={row} onClose={vi.fn()} /></QueryClientProvider>)
 for (const name of ['Model config', 'Dataset config', 'Skipped node']) expect(screen.queryByText(name)).not.toBeInTheDocument()
 for (const name of ['Answer agent', 'Trace agent', 'Failed agent', 'Active agent', 'Cancelled agent']) expect(screen.getByText(name)).toBeInTheDocument()
 expect(screen.getAllByRole('listitem')).toHaveLength(5)
})
it('resolves friendly names from the pinned publication when row results omit labels', async () => {
 const attempt = { run_id: 'run', status: 'completed', protocol_revision_id: 'publication-old', node_runs: { 'node-123': { status: 'completed', output_text: 'Answer' } } }
 const row = { row_result_id: 'slot', replicate_number: 1, protocol_revision_id: 'publication-old', dataset_row: { row_index: 0 }, latest_attempt: attempt, attempts: [attempt] }
 vi.spyOn(experimentsApi, 'getRunResults').mockResolvedValue({ row_results: [row] } as unknown as ExperimentRunResults)
 const revision = vi.spyOn(protocolsApi, 'getRevision').mockResolvedValue({ graph: { nodes: [{ id: 'node-123', type: 'agent', data: { label: 'Published researcher' } }], edges: [] } } as unknown as ProtocolRevision)
 render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ResultsInspectorPanel experimentId="e" experiment={{} as Experiment} selection={{ type: 'row', rowResultId: 'slot', scope: { protocol_id: 'p' } }} onClose={vi.fn()} /></QueryClientProvider>)
 expect(await screen.findByRole('button', { name: /Published researcher/ })).toBeInTheDocument()
 expect(screen.queryByText('Canvas node')).not.toBeInTheDocument()
 expect(revision).toHaveBeenCalledWith('p', 'publication-old')
})
it('shows published node names with expandable text outputs instead of raw attempt JSON', () => {
 const attempt = { run_id: 'run', status: 'completed', node_labels: { 'node-123': 'Researcher', 'node-456': 'Reviewer' },
  node_runs: { 'node-123': { status: 'completed', output_text: 'Research findings' }, 'node-456': { status: 'completed', output_text: 'Review answer' } },
  attempt_result: { internal_marker: 'Raw attempt dump' },
 }
 const row = { row_result_id: 'slot', replicate_number: 1, dataset_row: { row_index: 0 }, latest_attempt: attempt, attempts: [attempt] } as unknown as RowResult
 render(<QueryClientProvider client={new QueryClient()}><DatasetRowDetail row={row} onClose={vi.fn()} /></QueryClientProvider>)
 expect(screen.getByRole('button', { name: /Researcher/ })).toHaveAttribute('aria-expanded', 'false')
 expect(screen.getByRole('button', { name: /Reviewer/ })).toHaveAttribute('aria-expanded', 'true')
 expect(screen.queryByText('Research findings')).not.toBeInTheDocument()
 expect(screen.getByText('Review answer')).toBeInTheDocument()
 expect(screen.queryByText(/Raw attempt dump/)).not.toBeInTheDocument()
 expect(screen.queryByText('node-123')).not.toBeInTheDocument()
 fireEvent.click(screen.getByRole('button', { name: /Researcher/ }))
 expect(screen.getByText('Research findings')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button', { name: /Reviewer/ }))
 expect(screen.queryByText('Review answer')).not.toBeInTheDocument()
})
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
