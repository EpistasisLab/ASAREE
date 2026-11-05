import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { ResultMetric } from '@/lib/resultMetrics'
import type { CellMetricObservation, CellMetricObservationPage, ExecutionCell } from '@/types/executionResults'
import { CellMetricResults } from './CellMetricResults'

const cells: ExecutionCell[] = [{ cell_id: 'cell-1', cell_number: 7, cell_label: 'model=a', factor_values: {} }]
const observation: CellMetricObservation = {
  slot_id: 'slot-1', replicate_label: 'model=a-rep1', replicate_number: 1, row_index: null,
  execution_status: 'completed', status: 'measured', value_preview: '0', value_truncated: false,
  reason: null, exclusion_reason: null,
}
const page = (items: CellMetricObservation[], total = items.length, offset = 0): CellMetricObservationPage => ({
  cells: [{ cell_id: 'cell-1', items, total }], offset, limit: 20,
})

function mount(metric: ResultMetric = { key: 'metric-1', label: 'Selected metric' }) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}><CellMetricResults cells={cells} experimentId="experiment"
    scope={{ protocol_revision_id: 'published-version' }} metric={metric} generation={1} onSelectCell={vi.fn()} />
  </QueryClientProvider>)
}

describe('selected metric results by cell', () => {
  beforeEach(() => { vi.spyOn(experimentsApi, 'getCellMetricObservations') })

  it('abbreviates million-token recorded values without losing the exact count', async () => {
    vi.mocked(experimentsApi.getCellMetricObservations).mockResolvedValue(page([{ ...observation, value_preview: '1250000' }]))
    mount({ key: 'metric-1', label: 'Input tokens', unit: 'tokens' })
    expect(await screen.findByText('1.25M tokens')).toHaveAttribute('title', '1250000')
  })

  it('shows zero alongside the exact unavailable reason and excluded-attempt reason', async () => {
    vi.mocked(experimentsApi.getCellMetricObservations).mockResolvedValue(page([
      observation,
      { ...observation, slot_id: 'slot-2', replicate_number: 2, status: 'unavailable', value_preview: 'null', reason: 'No tool calls were attempted.' },
      { ...observation, slot_id: 'slot-3', replicate_number: 3, value_preview: '4', exclusion_reason: 'Excluded because the attempt reached its iteration limit.' },
    ]))
    mount()
    expect(await screen.findByText('0', { selector: 'pre' })).toBeVisible()
    expect(screen.getByText('No tool calls were attempted.')).toBeVisible()
    expect(screen.getByText('Excluded because the attempt reached its iteration limit.')).toBeVisible()
    expect(screen.getByText('Replicate 2')).toBeVisible()
    expect(experimentsApi.getCellMetricObservations).toHaveBeenCalledWith('experiment', { protocol_revision_id: 'published-version' }, 'metric-1', ['cell-1'], 0, expect.any(AbortSignal))
  })

  it('shows hashes, text, structured values, and measured null with replicate and row labels', async () => {
    vi.mocked(experimentsApi.getCellMetricObservations).mockResolvedValue(page([
      { ...observation, value_preview: '"abcd1234"' },
      { ...observation, slot_id: 'slot-2', replicate_number: 2, value_preview: '{"explanation":"No signal"}', row_index: 2 },
      { ...observation, slot_id: 'slot-3', replicate_number: 3, value_preview: 'null' },
    ]))
    mount()
    expect(await screen.findByText('abcd1234')).toBeVisible()
    expect(screen.getByText(/"explanation": "No signal"/)).toBeVisible()
    expect(screen.getByText('Replicate 2 · Row 3')).toBeVisible()
    expect(screen.getByText('null', { selector: 'pre' })).toBeVisible()
  })

  it('pages recorded values within a cell without fetching every execution', async () => {
    vi.mocked(experimentsApi.getCellMetricObservations)
      .mockResolvedValueOnce(page(Array.from({ length: 20 }, (_, index) => ({ ...observation, slot_id: `slot-${index}`, replicate_number: index + 1 })), 21))
      .mockResolvedValueOnce(page([{ ...observation, slot_id: 'slot-21', replicate_number: 21 }], 21, 20))
    mount()
    expect(await screen.findByText('Replicate 20')).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'Next measurements' }))
    expect(await screen.findByText('Replicate 21')).toBeVisible()
    expect(screen.getByText('21–21 of 21 executions')).toBeVisible()
    expect(experimentsApi.getCellMetricObservations).toHaveBeenLastCalledWith('experiment', expect.anything(), 'metric-1', ['cell-1'], 20, expect.any(AbortSignal))
  })

  it('reports read errors explicitly and supports retry', async () => {
    vi.mocked(experimentsApi.getCellMetricObservations).mockRejectedValueOnce(new Error('Results temporarily unavailable.')).mockResolvedValueOnce(page([observation]))
    mount()
    expect(await screen.findByRole('alert')).toHaveTextContent('Results temporarily unavailable.')
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }))
    await waitFor(() => expect(screen.getByText('0', { selector: 'pre' })).toBeVisible())
  })
})
