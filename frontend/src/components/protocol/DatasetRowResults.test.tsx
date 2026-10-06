import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { Experiment, ExperimentRunResults } from '@/types/experiments'
import { ResultsTab } from './ResultsTab'
import { DatasetRowResults } from './DatasetRowResults'

afterEach(() => vi.restoreAllMocks())
it('renders opaque values and distinct row output selections without ranking', async () => {
 const results = { consumption_mode: 'per_row', row_summary: { planned: 4, completed: 3, failed: 1, missing_reported: 1, metric_coverage: {} }, row_results: ['12', { answer: 2 }, null, undefined].map((value,index) => ({ row_result_id: `row-${index}`, factor_values: { Prompt: 'level' }, dataset_row: { row_index: index }, replicate_number: 2, status: index === 3 ? 'failed' : 'completed', latest_attempt: { run_id: `run-${index}` }, measurement: { observations: [{ metric_id: 'answer', status: index === 3 ? 'unavailable' : 'measured', value }] } })) } as unknown as ExperimentRunResults
 vi.spyOn(experimentsApi, 'getRunResults').mockResolvedValue(results)
 const experiment = { id: 'e', name: 'Rows', design_spec: { metrics: [{ id: 'answer', name: 'Answer', kind: 'custom' }] } } as unknown as Experiment
 const inspect = vi.fn()
 render(<QueryClientProvider client={new QueryClient()}><ResultsTab protocolId="p" experimentId="e" experimentName="Rows" experiment={experiment} onSelectResult={inspect} /></QueryClientProvider>)
 await screen.findByText('null')
 expect(screen.getByText('12')).toBeInTheDocument()
 expect(screen.getByText('{"answer":2}')).toBeInTheDocument()
 expect(screen.getByText('unavailable')).toBeInTheDocument()
 expect(screen.queryByText('Best current result')).not.toBeInTheDocument()
 fireEvent.click(screen.getAllByRole('button', { name: 'Inspect row' })[2])
 expect(inspect).toHaveBeenCalledWith({ type: 'row', rowResultId: 'row-2', scope: { protocol_id: 'p' } })
})

it('paginates dense rows, maximizes with Escape, and exports the selected scope', async () => {
 const scope = { protocol_id: 'p', protocol_revision_id: 'old', design_revision_id: 'design' }
 const results = { row_results: Array.from({ length: 21 }, (_, index) => ({ row_result_id: `slot-${index}`, factor_values: {}, dataset_row: { row_index: index }, replicate_number: 1, status: 'completed', latest_attempt: { run_id: `run-${index}` }, measurement: { observations: [] } })) } as unknown as ExperimentRunResults
 const csv = vi.spyOn(experimentsApi, 'downloadRunResultsCsv').mockResolvedValue(new Blob(['rows']))
 const create = vi.fn(() => 'blob:csv')
 Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: create })
 Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() })
 vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
 render(<DatasetRowResults results={results} experiment={{ id: 'e' } as Experiment} scope={scope} onInspect={vi.fn()} />)
 expect(screen.getAllByRole('button', { name: 'Inspect row' })).toHaveLength(20)
 fireEvent.click(screen.getByRole('button', { name: 'Next' }))
 expect(screen.getAllByRole('button', { name: 'Inspect row' })).toHaveLength(1)
 fireEvent.click(screen.getByRole('button', { name: 'Maximize Results' }))
 expect(document.body.style.overflow).toBe('hidden')
 fireEvent.keyDown(document, { key: 'Escape' })
 expect(document.body.style.overflow).not.toBe('hidden')
 fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
 expect(csv).toHaveBeenCalledWith('e', scope)
 await screen.findByRole('button', { name: 'Download CSV' })
})

it('shows generated cells with no outcomes and filters executions by cell identity', () => {
 const results = {
  row_cells: [
   { cell_id: 'a', cell_label: 'Prompt:short', factor_values: { Prompt: 'short' }, replicate_count: 1 },
   { cell_id: 'b', cell_label: 'Prompt:long', factor_values: { Prompt: 'long' }, replicate_count: 1 },
  ],
  row_results: [
   { row_result_id: 'slot-a', cell_id: 'a', cell_label: 'Prompt:short', factor_values: { Prompt: 'short' }, dataset_row: { row_index: 0 }, replicate_number: 1, status: 'completed', latest_attempt: { run_id: 'run-a' } },
  ],
 } as unknown as ExperimentRunResults
 render(<DatasetRowResults results={results} experiment={{ id: 'e' } as Experiment} scope={{}} onInspect={vi.fn()} />)
 fireEvent.click(screen.getByRole('button', { name: 'View cell Prompt:long' }))
 expect(screen.queryByRole('button', { name: 'Inspect row' })).not.toBeInTheDocument()
 expect(screen.getByText('No row executions planned for this cell.')).toBeInTheDocument()
 fireEvent.click(screen.getByRole('button', { name: 'All cells' }))
 expect(screen.getByRole('button', { name: 'Inspect row' })).toBeInTheDocument()
})

it('filters row outcomes to the selected replicate', () => {
 const results = {
  row_cells: [{ cell_id: 'a', cell_label: 'a', factor_values: {}, replicate_count: 2, replicates: [1, 2].map(number => ({ replicate_result_id: `parent-${number}`, replicate_label: `a-${number}`, replicate_number: number })) }],
  row_results: [1, 2].map(number => ({ row_result_id: `slot-${number}`, cell_id: 'a', cell_label: 'a', factor_values: {}, replicate_result_id: `parent-${number}`, replicate_number: number, dataset_row: { row_index: 0 }, status: 'completed', latest_attempt: { run_id: `run-${number}` } })),
 } as unknown as ExperimentRunResults
 const inspect = vi.fn()
 render(<DatasetRowResults results={results} experiment={{ id: 'e' } as Experiment} scope={{}} onInspect={inspect} />)
 fireEvent.click(screen.getByRole('button', { name: 'View cell a' }))
 expect(screen.getAllByRole('button', { name: 'View results' })).toHaveLength(1)
 fireEvent.click(screen.getByRole('button', { name: 'View a replicate 2' }))
 expect(screen.getAllByRole('button', { name: 'Inspect row' })).toHaveLength(1)
 fireEvent.click(screen.getByRole('button', { name: 'Inspect row' }))
 expect(inspect).toHaveBeenCalledWith('slot-2')
})
