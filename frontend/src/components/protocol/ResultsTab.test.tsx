import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, experimentsApi } from '@/api/client'
import { executionKeys } from '@/lib/executionQueries'
import type { AttemptDetail, ExecutionCell, ExecutionSlot, ExecutionSummary } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import { ResultsInspectorPanel, ResultsTab } from './ResultsTab'
import { ExecutionBrowser } from './ExecutionBrowser'

vi.mock('@/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, experimentsApi: { ...actual.experimentsApi,
    getExecutionSummary: vi.fn(), listExecutionCells: vi.fn(), listExecutions: vi.fn(),
    getExecutionFactorOptions: vi.fn(), listExecutionReplicates: vi.fn(),
    listExecutionAttempts: vi.fn(), getExecutionAttempt: vi.fn(), getRunResults: vi.fn(),
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
  sessionStorage.clear()
  vi.mocked(experimentsApi.getExecutionFactorOptions).mockResolvedValue({ factors: { model: ['a'] }, truncated: false })
  vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue(summary)
  vi.mocked(experimentsApi.listExecutionCells).mockResolvedValue({ items: [cell], next_cursor: null })
  vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [slot], next_cursor: null })
  vi.mocked(experimentsApi.listExecutionReplicates).mockResolvedValue({ items: [{
    cell_id: 'cell-1', replicate_id: 'parent-1', replicate_label: 'replicate-1', replicate_number: 1,
    total: 2, matching: 2, counts: { completed: 1, not_started: 1 },
  }], next_cursor: null })
  vi.mocked(experimentsApi.listExecutionAttempts).mockResolvedValue({ slot, items: [{ run_id: 'run-1', status: 'completed',
    created_at: '', completed_at: '', error: null, current: true, result: {}, usage: null }], next_cursor: null })
  vi.mocked(experimentsApi.getExecutionAttempt).mockResolvedValue(detail)
})
afterEach(() => { vi.clearAllMocks(); vi.useRealTimers() })

describe('bounded Results reads', () => {
  it('renders cells independently of summary preparation and fetches records only on expansion', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, ready: false })
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    expect(await screen.findByText(/Preparing the version summary/)).toBeInTheDocument()
    expect(await screen.findByRole('button', { name: 'View results' })).toBeInTheDocument()
    expect(experimentsApi.listExecutions).not.toHaveBeenCalled()
    expect(experimentsApi.getRunResults).not.toHaveBeenCalled()
    expect(experimentsApi.getExecutionAttempt).not.toHaveBeenCalled()
  })

  it('ignores historical primary ranking and sorts by a metric only when requested', async () => {
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Maximize Results' }))
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.body.style.overflow).not.toBe('hidden')
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: 'Sort cells' })).toHaveValue('label')
    expect(screen.getByRole('combobox', { name: 'Choose comparison metric' })).toHaveValue('')
    expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', expect.anything(),
      expect.objectContaining({ metric: undefined, sort: 'label' }), expect.any(AbortSignal))
    fireEvent.change(screen.getByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'duration_seconds' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort cells' }), { target: { value: 'metric' } })
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenCalledWith('experiment', expect.anything(),
      expect.objectContaining({ metric: 'duration_seconds', direction: 'minimize' }), expect.any(AbortSignal)))
  })

  it('paginates execution records on the server', async () => {
    vi.mocked(experimentsApi.listExecutions).mockImplementation(async (_id, _scope, params) =>
      params?.cursor ? { items: [{ ...slot, slot_id: 'slot-2', replicate_number: 2 }], next_cursor: null }
        : { items: [slot], next_cursor: 'page-2' })
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    const next = await screen.findAllByRole('button', { name: 'Next' })
    fireEvent.click(next.find(button => !button.hasAttribute('disabled'))!)
    expect(await screen.findByText('Replicate 2')).toBeInTheDocument()
    expect(experimentsApi.listExecutions).toHaveBeenCalledWith('experiment', expect.anything(),
      expect.objectContaining({ cell_id: 'cell-1', cursor: 'page-2' }), expect.any(AbortSignal))
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
    fireEvent.click(await screen.findByRole('button', { name: 'Replicate 1' }))
    expect(await screen.findByText(/Dataset row 1/)).toBeInTheDocument()
    expect(screen.getByText(/Dataset row 2/).closest('button')).toBeDisabled()
    fireEvent.click(screen.getByText(/Dataset row 1/).closest('button')!)
    expect(select).toHaveBeenCalledWith(expect.objectContaining({ type: 'execution', slotId: 'slot-1' }))
  })

  it('remembers Results controls independently of Runs and preserves them on return', async () => {
    sessionStorage.setItem('asaree:runs:experiment', JSON.stringify({ filters: { search: 'unrelated', status: 'failed', factors: {} }, sort: 'failed' }))
    const view = mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    await screen.findByRole('button', { name: /model: a/ })
    expect(screen.getByRole('searchbox')).toHaveValue('')
    expect(screen.getByRole('combobox', { name: 'Sort cells' })).toHaveValue('label')
    fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'model' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Filter by status' }), { target: { value: 'failed' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'duration_seconds' } })
    fireEvent.click(screen.getByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    view.unmount()
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    expect(await screen.findByText('Replicate 1')).toBeInTheDocument()
    expect(screen.getByRole('searchbox')).toHaveValue('model')
    expect(screen.getByRole('combobox', { name: 'Filter by status' })).toHaveValue('failed')
    expect(screen.getByRole('combobox', { name: 'Choose comparison metric' })).toHaveValue('duration_seconds')
    expect(screen.getByText('Entire version')).toBeInTheDocument()
    expect(screen.getByText(/Cell aggregates use all eligible replicates/)).toBeInTheDocument()
  })

  it('sorts replicates by the explicitly selected metric and offers recovery in Runs', async () => {
    vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [{ ...slot, status: 'failed' }], next_cursor: null })
    const recover = vi.fn()
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} onOpenRuns={recover} />)
    fireEvent.change(await screen.findByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'duration_seconds' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort replicates' }), { target: { value: 'metric' } })
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    const item = (await screen.findByText('Replicate 1')).closest('li')!
    fireEvent.click(within(item).getByRole('button', { name: 'Open in Runs' }))
    expect(recover).toHaveBeenCalledWith({ cellId: 'cell-1', replicateId: 'parent-1', rowIndex: undefined })
    expect(experimentsApi.listExecutions).toHaveBeenLastCalledWith('experiment', expect.anything(),
      expect.objectContaining({ sort: 'metric:duration_seconds', sort_direction: 'asc' }), expect.any(AbortSignal))
  })

  it('restarts replicate pagination when the comparison metric changes', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary,
      metric_types: { duration_seconds: 'number', cost_usd: 'number' } })
    vi.mocked(experimentsApi.listExecutions).mockImplementation(async (_id, _scope, params) => ({
      items: [{ ...slot, replicate_number: params?.cursor ? 2 : 1 }], next_cursor: params?.cursor ? null : 'metric-page-2',
    }))
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    fireEvent.change(await screen.findByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'duration_seconds' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort replicates' }), { target: { value: 'metric' } })
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    await screen.findByText('Replicate 1')
    fireEvent.click(screen.getAllByRole('button', { name: 'Next' }).find(button => !button.hasAttribute('disabled'))!)
    await screen.findByText('Replicate 2')
    fireEvent.change(screen.getByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'cost_usd' } })
    await screen.findByText('Replicate 1')
    expect(experimentsApi.listExecutions).toHaveBeenLastCalledWith('experiment', expect.anything(),
      expect.objectContaining({ sort: 'metric:cost_usd', cursor: undefined }), expect.any(AbortSignal))
  })

  it('remembers expanded row-mode replicate groups after returning to Results', async () => {
    vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue({ ...summary, unit: 'row' })
    vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [{ ...slot, row_index: 0 }], next_cursor: null })
    const view = mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: /model: a/ }))
    fireEvent.click(await screen.findByRole('button', { name: 'Replicate 1' }))
    await screen.findByText(/Dataset row 1/)
    view.unmount()
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    expect(await screen.findByText(/Dataset row 1/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Replicate 1' })).toHaveAttribute('aria-expanded', 'true')
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
    expect(screen.getByText(/1 of 2 metrics measured/)).toBeInTheDocument()
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

  it('preserves ranked pages on refresh until the server expires their cursor, then offers an explicit restart', async () => {
    let expired = false
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) => {
      if (expired && params?.cursor) throw new ApiError(422, 'stale_sort_cursor')
      return page(params?.cursor === 'page-2' ? 2 : 1)
    })
    const { client } = mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={vi.fn()} />)
    fireEvent.change(await screen.findByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'duration_seconds' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort cells' }), { target: { value: 'metric' } })
    await screen.findByRole('button', { name: /page: 1/ })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))
    await screen.findByRole('button', { name: /page: 2/ })
    expired = true
    vi.mocked(experimentsApi.listExecutionCells).mockClear()

    await act(async () => {
      client.setQueryData(executionKeys.summary('experiment', {}), { ...summary, generation: 2 })
    })

    const reload = await screen.findByRole('button', { name: 'Reload ranking' })
    expect(screen.getByRole('button', { name: /page: 2/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    expect(experimentsApi.listExecutionCells).toHaveBeenCalledTimes(1)
    fireEvent.click(reload)
    expect(await screen.findByRole('button', { name: /page: 1/ })).toBeInTheDocument()
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Reload ranking' })).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled()
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment', {},
      expect.objectContaining({ cursor: undefined, metric: 'duration_seconds' }), expect.any(AbortSignal)))
  })

  it('offers a restart when a cursor expires while loading a page for the first time', async () => {
    vi.mocked(experimentsApi.listExecutionCells).mockImplementation(async (_id, _scope, params) => {
      if (params?.cursor) throw new ApiError(422, 'stale_sort_cursor')
      return page(1)
    })
    mount(<ExecutionBrowser experimentId="experiment" scope={{}} mode="results" onSelect={vi.fn()} />)
    fireEvent.change(await screen.findByRole('combobox', { name: 'Choose comparison metric' }), { target: { value: 'duration_seconds' } })
    fireEvent.change(screen.getByRole('combobox', { name: 'Sort cells' }), { target: { value: 'metric' } })
    await screen.findByRole('button', { name: /page: 1/ })
    fireEvent.click(screen.getByRole('button', { name: 'Next' }))

    const reload = await screen.findByRole('button', { name: 'Reload ranking' })
    expect(screen.queryByText('stale_sort_cursor')).not.toBeInTheDocument()
    fireEvent.click(reload)
    expect(await screen.findByRole('button', { name: /page: 1/ })).toBeInTheDocument()
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled())
  })
})
