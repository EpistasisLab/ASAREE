import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import { executionKeys } from '@/lib/executionQueries'
import type { AttemptDetail, ExecutionCell, ExecutionSlot, ExecutionSummary } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import { ResultsInspectorPanel, ResultsTab } from './ResultsTab'
import { ExecutionBrowser } from './ExecutionBrowser'

vi.mock('@/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, experimentsApi: { ...actual.experimentsApi,
    getExecutionSummary: vi.fn(), listExecutionCells: vi.fn(), listExecutions: vi.fn(), getExecutionFactorOptions: vi.fn(),
    listExecutionAttempts: vi.fn(), getExecutionAttempt: vi.fn(), getRunResults: vi.fn(), getExecutionCell: vi.fn(),
  } }
})
vi.mock('./NodeRunOutputPanel', () => ({ ReceivedPromptPanel: () => null, RunStepTrace: () => null, UnresolvedReferencesNote: () => null }))

const experiment = { id: 'experiment', name: 'Measurements', design_spec: { metrics: [] } } as unknown as Experiment
const summary: ExecutionSummary = {
  scope_key: 'scope', unit: 'replicate', ready: true, protocol_id: 'protocol', protocol_revision_id: 'v1', design_revision_id: 'design',
  generation: 1, change_id: 1, refreshing: false, updated_at: '', counts: { completed: 15786 }, total: 15786,
  has_active: false, design_spec: null, metric_types: { duration_seconds: 'number' }, metric_aggregations: { duration_seconds: 'mean' },
  metric_directions: { duration_seconds: 'minimize' }, primary_metric: 'duration_seconds', primary_metric_direction: 'minimize',
}
const cell: ExecutionCell = { cell_id: 'cell-1', cell_label: 'model=a', factor_values: { model: 'a' },
  replicate_count: 15786, total: 15786, counts: { completed: 15786 }, metric_values: { duration_seconds: 65 } }
const slot: ExecutionSlot = { slot_id: 'slot-1', cell_id: 'cell-1', cell_label: 'model=a', factor_values: { model: 'a' },
  replicate_id: 'parent-1', replicate_label: 'replicate-1', replicate_number: 1, row_result_id: null, row_index: null,
  dataset_id: null, raw_sha256: null, run_id: 'run-1', status: 'completed', updated_at: '', error: null, result: {}, usage: null }
const detail: AttemptDetail = { run_id: 'run-1', status: 'completed', error: null, started_at: '', completed_at: '',
  protocol_revision_id: 'v1', dataset_row: null, node_runs: {}, node_labels: {},
  usage: { cost_usd: 0.0123, total_tokens: 200, input_tokens: 100, output_tokens: 100, duration_seconds: 65, agent_run_count: 1 },
  attempt_result: { measurement: { observations: [], artifacts: [] } } }

function mount(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return { ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>), client }
}
beforeEach(() => {
  vi.mocked(experimentsApi.getExecutionFactorOptions).mockResolvedValue({ factors: { model: ['a', 'b'] }, truncated: false })
  vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue(summary)
  vi.mocked(experimentsApi.listExecutionCells).mockResolvedValue({ items: [cell], next_cursor: null })
  vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [slot], next_cursor: null })
  vi.mocked(experimentsApi.listExecutionAttempts).mockResolvedValue({ slot, items: [{ run_id: 'run-1', status: 'completed',
    created_at: '', completed_at: '', error: null, current: true, result: {}, usage: null }], next_cursor: null })
  vi.mocked(experimentsApi.getExecutionAttempt).mockResolvedValue(detail)
})
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers() })

describe('bounded Results reads', () => {
  it('shows an informational Results cell status pill while keeping filtering global', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, cell_count: 1,
      cell_result_counts: { ready: 0, needs_attention: 1, awaiting_results: 0 } })
    vi.mocked(experimentsApi.listExecutionCells).mockResolvedValue({ items: [{ ...cell, result_status: 'needs_attention' }], total: 1, next_cursor: null })
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={vi.fn()} />)
    const cellToggle = await screen.findByRole('button', { name: /model: a/ })
    expect(within(cellToggle).getByText('Needs attention')).toBeInTheDocument()
    expect(within(cellToggle).queryByRole('button')).not.toBeInTheDocument()
    fireEvent.click(cellToggle)
    await screen.findByText('Replicate 1')
    expect(screen.getByRole('button', { name: 'Filter cells by Total cells' })).toHaveAttribute('aria-pressed', 'true')
    expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: undefined, status: undefined }, expect.any(AbortSignal))
  })

  it.each([
    ['ready', 'Ready to review'], ['needs_attention', 'Needs attention'], ['awaiting_results', 'Awaiting results'],
  ] as const)('filters Results cells by %s across pages and combines with search', async (status, label) => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, cell_count: 24,
      cell_result_counts: { ready: 12, needs_attention: 5, awaiting_results: 7 } })
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) => ({
      items: [cell], total: 12, next_cursor: params?.cursor ? null : 'page-2',
    }))
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={vi.fn()} />)
    fireEvent.change(await screen.findByRole('searchbox'), { target: { value: 'model' } })
    fireEvent.click(await screen.findByRole('button', { name: `Filter cells by ${label}` }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: 'model', status: undefined, result_status: status }, expect.any(AbortSignal)))
    expect(screen.getByRole('button', { name: `Filter cells by ${label}` })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ cursor: 'page-2', result_status: status }), expect.any(AbortSignal)))
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    expect(experimentsApi.listExecutions).toHaveBeenLastCalledWith('experiment', {},
      { cell_id: 'cell-1', cursor: undefined, limit: 10, search: 'model', status: undefined }, expect.any(AbortSignal))
    fireEvent.click(screen.getByRole('button', { name: 'Filter cells by Total cells' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: 'model', status: undefined }, expect.any(AbortSignal)))
    expect(screen.getByRole('button', { name: 'Filter cells by Total cells' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: `Filter cells by ${label}` }))
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Filter cells by Total cells' })).toHaveAttribute('aria-pressed', 'true')
  })

  it('shows cell measurements as dashboard cards with friendly labels and formatted values', async () => {
    vi.mocked(experimentsApi.getExecutionCell).mockResolvedValue({ ...cell, metric_values: {
      cost_usd: 0.1234, total_tokens: 12500, duration_seconds: 65, reported_auc: 0.9723,
    } })
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary,
      metric_labels: { reported_auc: 'ROC AUC' }, metric_aggregations: { cost_usd: 'sum', reported_auc: 'mean' } })
    mount(<ResultsInspectorPanel experimentId="experiment" experiment={experiment}
      selection={{ type: 'cell', cellId: 'cell-1', cellLabel: 'model=a', scope: {} }} onClose={vi.fn()} />)
    const dashboard = within(await screen.findByRole('region', { name: 'Cell measurements' }))
    await waitFor(() => expect(dashboard.getByText('ROC AUC')).toBeInTheDocument())
    expect(dashboard.getByText('Cost')).toBeInTheDocument()
    expect(dashboard.getByText('$0.12')).toBeInTheDocument()
    expect(dashboard.getByText('Total tokens')).toBeInTheDocument()
    expect(dashboard.getByText('12,500')).toBeInTheDocument()
    expect(dashboard.getByText('65 sec')).toBeInTheDocument()
    expect(dashboard.getByText('0.9723')).toBeInTheDocument()
    expect(dashboard.getByText('Total')).toBeInTheDocument()
    expect(dashboard.getByText('Average')).toBeInTheDocument()
    expect(dashboard.queryByText('cost_usd')).not.toBeInTheDocument()
    expect(await screen.findByText('Replicate 1')).toBeInTheDocument()
  })

  it('keeps measurements and usage in the details view instead of Results replicate rows', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, design_spec: { metrics: [
      { id: 'runtime-cost', name: 'Cost', kind: 'runtime', catalogKey: 'cost_usd' },
      { id: 'runtime-duration', name: 'Duration', kind: 'runtime', catalogKey: 'duration_seconds' },
      { id: 'runtime-tokens', name: 'Total tokens', kind: 'runtime', catalogKey: 'total_tokens' },
    ] } })
    vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [{ ...slot, usage: detail.usage,
      result: { observations: [
        { metric_id: 'roc', metric_name: 'ROC AUC', preview: '0.97', status: 'measured', preview_truncated: false },
        { metric_id: 'runtime-cost', metric_name: 'Cost', preview: '0.0123', status: 'measured', preview_truncated: false },
        { metric_id: 'runtime-duration', metric_name: 'Duration', preview: '65', status: 'measured', preview_truncated: false },
        { metric_id: 'runtime-tokens', metric_name: 'Total tokens', preview: '200', status: 'measured', preview_truncated: false },
      ] } }], next_cursor: null })
    const select = vi.fn()
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={select} />)
    await screen.findByText(/Usage available for/)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    const row = (await screen.findByText('Replicate 1')).closest('button')!
    expect(within(row).queryByText('ROC AUC: 0.97')).not.toBeInTheDocument()
    expect(within(row).getByText('Completed')).toBeInTheDocument()
    expect(within(row).queryByText(/Cost:|Duration:|Total tokens:|\$0\.01|200 tokens/)).not.toBeInTheDocument()
    fireEvent.click(row)
    expect(select).toHaveBeenCalledWith({ type: 'execution', slotId: 'slot-1', scope: {} })
  })

  it('compares current-result and all-attempt cost, tokens and duration', async () => {
    const usage = { cost_usd: 0, total_tokens: 0, duration_seconds: 0, input_tokens: null, output_tokens: null, agent_run_count: null }
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, usage,
      all_attempt_usage: { ...usage, cost_usd: 0.12, total_tokens: 1200, duration_seconds: 65 }, usage_complete_count: 29, total: 576 })
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={vi.fn()} />)
    const table = within(await screen.findByRole('table', { name: 'Usage by attempt scope' }))
    const current = within(table.getByRole('row', { name: /Current results/ }))
    expect(current.getByText('$0.00')).toBeInTheDocument()
    expect(current.getByText('0')).toBeInTheDocument()
    expect(current.getByText('0 s')).toBeInTheDocument()
    const all = within(table.getByRole('row', { name: /All attempts/ }))
    expect(all.getByText('$0.12')).toBeInTheDocument()
    expect(all.getByText('1,200')).toBeInTheDocument()
    expect(all.getByText('65 s')).toBeInTheDocument()
    expect(screen.getByText('Usage available for 29 of 576 replicates.')).toBeInTheDocument()
    expect(screen.queryByText(/spend/i)).not.toBeInTheDocument()
  })

  it('keeps Results search, usage, status badges and export without execution tiles', async () => {
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    await screen.findByText(/Usage available for/)
    expect(screen.queryByRole('list', { name: 'Run status counts' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Filter by All' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Cell status counts')).not.toBeInTheDocument()
    expect(screen.queryByText('15786/15786 complete')).not.toBeInTheDocument()
    expect(screen.getByText('15786 replicates')).toBeInTheDocument()
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'model' } })
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', expect.anything(),
      { cursor: undefined, limit: 10, search: 'model', status: undefined }, expect.any(AbortSignal)))
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    expect(screen.getByText('Completed')).toBeInTheDocument()
  })

  it('keeps per-status counts in Runs and grays out those excluded by the global filter', async () => {
    const mode = 'runs'
    vi.mocked(experimentsApi.listExecutionCells).mockResolvedValue({ items: [{ ...cell, result_status: 'needs_attention', counts: { cancelled: 2, failed: 3 } }], next_cursor: null })
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode={mode} onSelect={vi.fn()} />)
    await screen.findByLabelText('Cell status counts')
    expect(screen.queryByText('Needs attention')).not.toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Filter by Cancelled' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ status: 'cancelled' }), expect.any(AbortSignal)))
    const counts = within(screen.getByLabelText('Cell status counts'))
    expect(counts.getByText('3 Failed')).toHaveClass('text-muted-foreground', 'opacity-50')
    expect(counts.getByText('2 Cancelled')).not.toHaveClass('text-muted-foreground')
    fireEvent.click(screen.getByRole('button', { name: 'Filter by All' }))
    expect(screen.getByRole('button', { name: 'Filter by All' })).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(within(screen.getByLabelText('Cell status counts')).getByText('3 Failed')).toHaveClass('text-destructive', 'opacity-70'))
  })

  it('reports every execution status in the Runs summary', async () => {
    const mode = 'runs'
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, total: 576,
      counts: { completed: 26, running: 1, pending: 2, failed: 5, not_started: 533, finalizing: 3, cancelled: 4, limit_reached: 2 } })
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode={mode} onSelect={vi.fn()} />)
    expect(await screen.findByRole('button', { name: 'Filter by All' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByText('26/576 replicates complete')).not.toBeInTheDocument()
    const tiles = within(screen.getByRole('list', { name: 'Run status counts' })).getAllByRole('listitem')
    expect(tiles).toHaveLength(8)
    expect(screen.queryByRole('button', { name: 'Filter by Finalizing' })).not.toBeInTheDocument()
    for (const [status, count] of [['All', 576], ['Not started', 533], ['Queued', 2], ['Running', 4], ['Completed', 26], ['Failed', 5], ['Cancelled', 4], ['Hit iteration limit', 2]] as const) {
      const tile = tiles.find(tile => within(tile).queryByText(status))!
      expect(within(tile).getByText(count.toLocaleString())).toBeInTheDocument()
    }
  })

  it.each(['runs', 'results'] as const)('shows Refresh only after a failed load or refresh in %s', async mode => {
    const { client } = mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode={mode} onSelect={vi.fn()} />)
    await screen.findByRole('button', { name: /model: a/ })
    if (mode === 'runs') expect(screen.queryByRole('button', { name: 'View results' })).not.toBeInTheDocument()
    else expect(screen.getByRole('button', { name: 'View results' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument()

    vi.mocked(experimentsApi.listExecutionCells).mockRejectedValue(new Error('Cells unavailable'))
    await act(async () => { await client.invalidateQueries({ queryKey: executionKeys.root('experiment') }) })
    expect(await screen.findByText('Refresh failed; showing the last loaded cells.')).toBeInTheDocument()
    vi.mocked(experimentsApi.listExecutionCells).mockResolvedValue({ items: [cell], next_cursor: null })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument())
  })

  it('offers Refresh when execution rows fail to load and hides it after recovery', async () => {
    vi.mocked(experimentsApi.listExecutions).mockRejectedValue(new Error('Executions unavailable'))
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="runs" onSelect={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Executions unavailable')
    vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [slot], next_cursor: null })
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }))
    await screen.findByText('Replicate 1')
    expect(screen.queryByRole('button', { name: 'Refresh' })).not.toBeInTheDocument()
  })

  it('renders cells independently of summary preparation and fetches records only on expansion', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, ready: false })
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    expect(await screen.findByText(/Preparing the version summary/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'View results' })).toBeInTheDocument()
    expect(experimentsApi.listExecutions).not.toHaveBeenCalled()
    expect(experimentsApi.getRunResults).not.toHaveBeenCalled()
    expect(experimentsApi.getExecutionAttempt).not.toHaveBeenCalled()
  })

  it('uses fixed ordering without primary-metric controls and preserves maximize/export', async () => {
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    const header = within(screen.getByRole('heading', { name: 'Current results' }).parentElement!)
    expect(header.getByRole('button', { name: 'Maximize Results' })).toBeInTheDocument()
    expect(header.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
    fireEvent.click(await screen.findByRole('button', { name: 'Maximize Results' }))
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.body.style.overflow).not.toBe('hidden')
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Choose comparison metric' })).not.toBeInTheDocument()
    expect(screen.queryByRole('combobox', { name: 'Sort cells' })).not.toBeInTheDocument()
    expect(experimentsApi.listExecutionCells).toHaveBeenCalledWith('experiment', expect.anything(),
      { cursor: undefined, limit: 10, search: undefined, status: undefined }, expect.any(AbortSignal))
  })

  it('combines search and status across pages in Runs', async () => {
    const mode = 'runs'
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) => ({
      items: [{ ...cell, cell_id: params?.cursor ? 'cell-2' : 'cell-1' }], next_cursor: params?.cursor ? null : 'page-2',
    }))
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode={mode} onSelect={vi.fn()} />)
    fireEvent.change(await screen.findByRole('searchbox'), { target: { value: 'model' } })
    fireEvent.click(await screen.findByRole('button', { name: 'Filter by Failed' }))
    expect(screen.getByRole('button', { name: 'Filter by Failed' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(experimentsApi.getExecutionFactorOptions).not.toHaveBeenCalled()
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: 'model', status: 'failed' }, expect.any(AbortSignal)))
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ cursor: 'page-2', search: 'model', status: 'failed' }), expect.any(AbortSignal)))
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    expect(screen.queryByRole('button', { name: /View result/ })).not.toBeInTheDocument()
    expect(experimentsApi.listExecutions).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ cell_id: 'cell-2', cursor: undefined, search: 'model', status: 'failed' }), expect.any(AbortSignal))
    fireEvent.click(screen.getByRole('button', { name: 'Filter by All' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: 'model', status: undefined }, expect.any(AbortSignal)))
    expect(screen.getByRole('searchbox')).toHaveValue('model')
    expect(screen.getByRole('button', { name: 'Filter by All' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: undefined, status: undefined }, expect.any(AbortSignal)))
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByRole('button', { name: 'Filter by Failed' })).toHaveAttribute('aria-pressed', 'false')
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument()
    expect(screen.queryAllByRole('button', { name: 'Previous' }).every(button => button.hasAttribute('disabled'))).toBe(true)
    fireEvent.click(screen.getByRole('button', { name: 'Filter by Failed' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: undefined, status: 'failed' }, expect.any(AbortSignal)))
    fireEvent.click(screen.getByRole('button', { name: 'Filter by Failed' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      { cursor: undefined, limit: 10, search: undefined, status: undefined }, expect.any(AbortSignal)))
  })

  it('paginates execution records on the server', async () => {
    vi.mocked(experimentsApi.listExecutions).mockImplementation(async (_id, _scope, params) =>
      ({ items: Array.from({ length: params?.cursor ? 3 : 10 }, (_, index) => {
        const number = (params?.cursor ? 11 : 1) + index
        return { ...slot, slot_id: `slot-${number}`, replicate_number: number }
      }), total: 13, next_cursor: params?.cursor ? null : 'page-2' }))
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    expect(screen.getByText('Showing 1–10 of 13 replicates')).toBeInTheDocument()
    const next = await screen.findAllByRole('button', { name: 'Next' })
    fireEvent.click(next.find(button => !button.hasAttribute('disabled'))!)
    expect(await screen.findByText('Replicate 11')).toBeInTheDocument()
    expect(screen.getByText('Showing 11–13 of 13 replicates')).toBeInTheDocument()
    expect(experimentsApi.listExecutions).toHaveBeenCalledWith('experiment', expect.anything(),
      expect.objectContaining({ cell_id: 'cell-1', cursor: 'page-2', limit: 10 }), expect.any(AbortSignal))
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('Showing 1–10 of 13 replicates')).toBeInTheDocument()
  })

  it.each(['runs', 'results'] as const)('shows record ranges for ten-cell pages in %s', async mode => {
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) => ({
      items: Array.from({ length: params?.cursor ? 2 : 10 }, (_, index) => ({ ...cell, cell_id: `cell-${params?.cursor ?? 'first'}-${index}` })),
      total: 12,
      next_cursor: params?.cursor ? null : 'page-2',
    }))
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode={mode} onSelect={vi.fn()} />)
    expect(await screen.findByText('Showing 1–10 of 12 cells')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    expect(await screen.findByText('Showing 11–12 of 12 cells')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByText('Showing 1–10 of 12 cells')).toBeInTheDocument()
  })

  it('labels per-row executions by source row and only inspects attempted slots', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, unit: 'row', primary_metric: null })
    vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [
      { ...slot, row_result_id: 'slot-1', row_index: 0 },
      { ...slot, slot_id: 'unplanned:parent-1:1', replicate_number: 2, row_index: 1, run_id: null, status: 'not_started' },
    ], next_cursor: null })
    const select = vi.fn()
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={select} />)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    expect(await screen.findByText(/Source row 1/)).toBeInTheDocument()
    expect(screen.getByText(/Source row 2/).closest('button')).toBeDisabled()
    fireEvent.click(screen.getByText(/Source row 1/).closest('button')!)
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ type: 'execution', slotId: 'slot-1' }))
  })

  it('fetches the selected attempt independently of the experiment list', async () => {
    mount(<ResultsInspectorPanel experimentId="experiment" experiment={experiment}
      selection={{ type: 'execution', slotId: 'slot-1', scope: { protocol_revision_id: 'v1' } }} onClose={vi.fn()} />)
    expect(await screen.findByText('$0.01')).toBeInTheDocument()
    expect(screen.getByText('1.1 min')).toBeInTheDocument()
    expect(experimentsApi.getExecutionAttempt).toHaveBeenCalledWith('experiment', 'run-1',
      { protocol_revision_id: 'v1' }, expect.any(AbortSignal))
    expect(experimentsApi.getRunResults).not.toHaveBeenCalled()
    expect(experimentsApi.listExecutions).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: 'Previous' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument()
  })

  it('preserves row pages on refresh and resets them when the source count becomes available', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, unit: 'row' })
    vi.mocked(experimentsApi.listExecutions).mockImplementation(async (_id, _scope, params) => ({
      items: [{ ...slot, replicate_number: params?.cursor ? 2 : 1 }], next_cursor: params?.cursor ? null : 'rows-page-2',
    }))
    const { client } = mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' }).find(button => !button.hasAttribute('disabled'))!)
    await screen.findByText('Replicate 2')
    await act(async () => {
      client.setQueryData(executionKeys.summary('experiment', {}), { ...summary, unit: 'row', generation: 2 })
    })
    expect(screen.getByText('Replicate 2')).toBeInTheDocument()
    await act(async () => {
      client.setQueryData(executionKeys.summary('experiment', {}), { ...summary, unit: 'row', generation: 3, source_row_count: 3 })
    })
    await screen.findByText('Replicate 1')
    expect(experimentsApi.listExecutions).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ cursor: undefined }), expect.any(AbortSignal))
  })

  it('preserves measured-null versus unavailable reported values and provenance', async () => {
    vi.mocked(experimentsApi.getExecutionAttempt).mockResolvedValue({ ...detail, attempt_result: { measurement: { observations: [
      { metric_id: 'report', metric_name: 'Report', status: 'measured', value: null, error: null, attempt_id: 'run-1',
        producer: { binding_id: 'reported', producer_id: 'asaree.agent_output', kind: 'reported', version: '1' }, input_provenance: {} },
      { metric_id: 'absent', metric_name: 'Absent', status: 'unavailable', value: null, error: 'Agent did not complete', attempt_id: 'run-1',
        producer: { binding_id: 'reported', producer_id: 'asaree.agent_output', kind: 'reported', version: '1' }, input_provenance: {} },
    ], artifacts: [] } } })
    mount(<ResultsInspectorPanel experimentId="experiment" experiment={experiment} selection={{ type: 'execution', slotId: 'slot-1', scope: {} }} onClose={vi.fn()} />)
    expect(await screen.findByText('Report')).toBeInTheDocument()
    expect(screen.getByText('null')).toBeInTheDocument()
    expect(screen.getByText('Agent did not complete')).toBeInTheDocument()
    expect(screen.getAllByText('asaree.agent_output · v1')).toHaveLength(2)
  })

  it('stops polling settled experiments', async () => {
    vi.useFakeTimers()
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    await act(async () => { await vi.advanceTimersByTimeAsync(100) })
    const calls = vi.mocked(experimentsApi.getExecutionSummary).mock.calls.length
    await act(async () => { await vi.advanceTimersByTimeAsync(15000) })
    expect(experimentsApi.getExecutionSummary).toHaveBeenCalledTimes(calls)
  })

  it('aborts abandoned list requests', async () => {
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(() => new Promise(() => {}))
    const view = mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    const signal = vi.mocked(experimentsApi.listExecutionCells).mock.calls[0][3]!
    view.unmount()
    expect(signal.aborted).toBe(true)
  })
})

describe('cell pagination during summary refreshes', () => {
  const page = (number: number) => ({
    items: [{ ...cell, cell_id: `cell-${number}`, cell_label: `page-${number}`, factor_values: { page: number } }],
    next_cursor: number < 3 ? `page-${number + 1}` : null,
  })

  it.each([
    { mode: 'runs' as const, primary_metric: null },
    { mode: 'results' as const, primary_metric: null },
    { mode: 'results' as const, primary_metric: 'duration_seconds' },
  ])('keeps the last $mode page and its history after a progress refresh (metric=$primary_metric)', async ({ mode, primary_metric }) => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, primary_metric })
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) =>
      page(params?.cursor === 'page-3' ? 3 : params?.cursor === 'page-2' ? 2 : 1))
    const { client } = mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode={mode} onSelect={vi.fn()} />)
    await screen.findByRole('button', { name: /page: 1/ })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('button', { name: /page: 2/ })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('button', { name: /page: 3/ })
    vi.mocked(experimentsApi.listExecutionCells).mockClear()

    await act(async () => {
      client.setQueryData(executionKeys.summary('experiment', {}), { ...summary, primary_metric, generation: 2 })
    })

    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenCalled())
    expect(vi.mocked(experimentsApi.listExecutionCells).mock.calls.every(call => call[2]?.cursor === 'page-3')).toBe(true)
    expect(screen.getByRole('button', { name: /page: 3/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled())
    fireEvent.click(screen.getByRole('button', { name: 'Previous' }))
    expect(await screen.findByRole('button', { name: /page: 2/ })).toBeInTheDocument()
  })

  it('does not cancel last-page navigation when a new generation arrives mid-request', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, primary_metric: null })
    let finishLastPage!: (value: ReturnType<typeof page>) => void
    const lastPage = new Promise<ReturnType<typeof page>>(resolve => { finishLastPage = resolve })
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) =>
      params?.cursor === 'page-3' ? lastPage : page(params?.cursor === 'page-2' ? 2 : 1))
    const { client } = mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="runs" onSelect={vi.fn()} />)
    await screen.findByRole('button', { name: /page: 1/ })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('button', { name: /page: 2/ })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ cursor: 'page-3' }), expect.any(AbortSignal)))

    await act(async () => {
      client.setQueryData(executionKeys.summary('experiment', {}), { ...summary, primary_metric: null, generation: 2 })
      finishLastPage(page(3))
    })

    expect(await screen.findByRole('button', { name: /page: 3/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeEnabled()
  })

})
