import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { AttemptPage, UsageDistribution, UsageRange } from '@/types/executionResults'
import { ReplicateUsageDistribution } from './ReplicateUsageDistribution'

vi.mock('@/api/client', () => ({ experimentsApi: { getUsageDistribution: vi.fn(), listExecutionAttempts: vi.fn() } }))

const scope = { protocol_revision_id: 'version-1' }
const distribution: UsageDistribution = {
  metric: 'total_tokens', unit: 'replicate', count: 1000, completed_count: 1005,
  minimum: 0, maximum: 120, median: 80, p25: 20, p75: 90,
  bins: [{ lower: 0, upper: 60, upper_inclusive: false, count: 100 },
    { lower: 60, upper: 120, upper_inclusive: true, count: 900 }],
}

function mount(props: { selectedSlotId?: string; onRangeChange?: (range: UsageRange | null) => void } = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>
    <ReplicateUsageDistribution experimentId="experiment" cellId="cell" scope={scope} {...props} />
  </QueryClientProvider>)
}

beforeEach(() => { vi.mocked(experimentsApi.getUsageDistribution).mockResolvedValue(distribution) })
afterEach(() => { vi.resetAllMocks() })

it('shows a full-cell distribution and missing-value coverage, independently of replicate pages', async () => {
  mount()
  expect(await screen.findByRole('group', { name: /Tokens distribution/ })).toBeInTheDocument()
  expect(screen.getByText('1,000 of 1,005 completed replicates with recorded tokens.')).toBeInTheDocument()
  expect(screen.getByText('80')).toBeInTheDocument()
  expect(screen.getByText('20–90')).toBeInTheDocument()
  expect(experimentsApi.getUsageDistribution).toHaveBeenCalledWith('experiment', scope, 'cell', 'total_tokens', expect.any(AbortSignal))
  const buttons = within(screen.getByRole('group', { name: 'Distribution metric' })).getAllByRole('button')
  expect(buttons.map(button => button.textContent)).toEqual(['Tokens', 'Cost', 'Duration'])
})

it('switches between cost and duration and preserves sub-cent cost precision', async () => {
  vi.mocked(experimentsApi.getUsageDistribution).mockImplementation(async (_experiment, _scope, _cell, metric) => ({
    ...distribution, metric, median: metric === 'cost_usd' ? 0.000123 : 65,
  }))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Cost' }))
  expect(await screen.findByText('$0.000123')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Cost' })).toHaveAttribute('aria-pressed', 'true')
  fireEvent.click(screen.getByRole('button', { name: 'Duration' }))
  expect(await screen.findByText('65 sec')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Duration' })).toHaveAttribute('aria-pressed', 'true')
})

it('handles identical zero values and labels row executions accurately', async () => {
  vi.mocked(experimentsApi.getUsageDistribution).mockResolvedValue({ ...distribution, unit: 'row', count: 7,
    completed_count: 7, minimum: 0, maximum: 0, median: 0, p25: 0, p75: 0,
    bins: [{ lower: 0, upper: 0, upper_inclusive: true, count: 7 }],
  })
  mount()
  expect(await screen.findByText('7 of 7 completed rows with recorded tokens.')).toBeInTheDocument()
  expect(screen.getByText('All values: 0')).toBeInTheDocument()
  expect(screen.getByText('Tokens per row')).toBeInTheDocument()
})

it('explains missing data without drawing an empty chart', async () => {
  vi.mocked(experimentsApi.getUsageDistribution).mockResolvedValue({ ...distribution, count: 0,
    minimum: null, maximum: null, median: null, p25: null, p75: null, bins: [],
  })
  mount()
  expect(await screen.findByText('No completed replicates with recorded tokens yet.')).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
})

it('allows retrying a failed distribution request', async () => {
  vi.mocked(experimentsApi.getUsageDistribution).mockRejectedValueOnce(new Error('Unavailable'))
  mount()
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not load the usage distribution.')
  fireEvent.click(screen.getByRole('button', { name: 'Retry distribution' }))
  await waitFor(() => expect(screen.getByRole('group', { name: /Tokens distribution/ })).toBeInTheDocument())
})

it('selects ranges with the keyboard, toggles them off, and clears them when changing metric', async () => {
  const onRangeChange = vi.fn()
  mount({ onRangeChange })
  const chart = within(await screen.findByRole('group', { name: 'Tokens distribution' }))
  const first = chart.getByRole('button', { name: '0 ≤ value < 60: 100 replicates' })
  fireEvent.keyDown(first, { key: 'Enter' })
  expect(first).toHaveAttribute('aria-pressed', 'true')
  expect(onRangeChange).toHaveBeenLastCalledWith({ metric: 'total_tokens', lower: 0, upper: 60, upper_inclusive: false })
  fireEvent.click(first)
  expect(onRangeChange).toHaveBeenLastCalledWith(null)
  const last = chart.getByRole('button', { name: '60 ≤ value ≤ 120: 900 replicates' })
  fireEvent.click(last)
  expect(onRangeChange).toHaveBeenLastCalledWith({ metric: 'total_tokens', lower: 60, upper: 120, upper_inclusive: true })
  fireEvent.click(screen.getByRole('button', { name: 'Cost' }))
  expect(onRangeChange).toHaveBeenLastCalledWith(null)
  expect(screen.queryByRole('button', { name: 'Clear range' })).not.toBeInTheDocument()
})

function selectedAttempt(tokens: number | null): AttemptPage {
  return { slot: { slot_id: 'selected', cell_id: 'cell', cell_label: 'a', factor_values: {},
    replicate_id: 'replicate', replicate_label: 'rep7', replicate_number: 7,
    row_result_id: null, row_index: null, dataset_id: null, raw_sha256: null, run_id: 'run',
    status: 'completed', updated_at: null, error: null, result: {},
    usage: { total_tokens: tokens, input_tokens: null, output_tokens: null, cost_usd: null, duration_seconds: null, agent_run_count: 1 },
  }, items: [], next_cursor: null }
}

it('marks the selected replicate using the latest attempt instead of its list page', async () => {
  vi.mocked(experimentsApi.listExecutionAttempts).mockResolvedValue(selectedAttempt(80))
  mount({ selectedSlotId: 'selected' })
  expect(await screen.findByRole('img', { name: 'Replicate 7: 80 on distribution' })).toBeInTheDocument()
  expect(experimentsApi.listExecutionAttempts).toHaveBeenCalledWith('experiment', 'selected', scope, undefined, expect.any(AbortSignal))
})

it('explains a selected replicate with no recorded value without marking it as zero', async () => {
  vi.mocked(experimentsApi.listExecutionAttempts).mockResolvedValue(selectedAttempt(null))
  mount({ selectedSlotId: 'selected' })
  expect(await screen.findByText('Replicate 7 · latest attempt: No completed value recorded')).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
})
