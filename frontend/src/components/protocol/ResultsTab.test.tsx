import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { AttemptDetail, ExecutionCell, ExecutionSlot, ExecutionSummary } from '@/types/executionResults'
import type { Experiment } from '@/types/experiments'
import { ResultsInspectorPanel, ResultsTab } from './ResultsTab'

vi.mock('@/api/client', async importOriginal => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, experimentsApi: { ...actual.experimentsApi,
    getExecutionSummary: vi.fn(), listExecutionCells: vi.fn(), listExecutions: vi.fn(),
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
  vi.mocked(experimentsApi.getExecutionSummary).mockResolvedValue(summary)
  vi.mocked(experimentsApi.listExecutionCells).mockResolvedValue({ items: [cell], next_cursor: null })
  vi.mocked(experimentsApi.listExecutions).mockResolvedValue({ items: [slot], next_cursor: null })
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

  it('uses server metric ordering and preserves the shared maximize controls', async () => {
    mount(<ResultsTab experimentId="experiment" experimentName="Measurements" experiment={experiment} onSelectResult={vi.fn()} />)
    fireEvent.click(await screen.findByRole('button', { name: 'Maximize Results' }))
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(document.body.style.overflow).not.toBe('hidden')
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeInTheDocument()
    await screen.findByRole('combobox', { name: 'Choose comparison metric' })
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
      { cell_id: 'cell-1', cursor: 'page-2' }, expect.any(AbortSignal))
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
