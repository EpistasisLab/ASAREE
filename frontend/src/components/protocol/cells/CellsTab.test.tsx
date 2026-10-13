import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { CellSummary, Experiment } from '@/types/experiments'
import { CellsTab } from './CellsTab'

const experiment = { id: 'e', name: 'Large', design_type: 'factorial', design_spec: null } as unknown as Experiment
const summary = (label: string, model: string, scored: number, sum: number): CellSummary => ({
  cell_id: label, cell_label: label, factor_values: { model }, replicate_count: 7893, scored_count: scored,
  updated_at: '2026-10-01T00:00:00Z', metrics: { accuracy: { sum, count: scored } },
})

afterEach(() => vi.restoreAllMocks())

it('renders large designs from per-cell aggregates without loading replicates', async () => {
  const replicates = vi.spyOn(experimentsApi, 'listReplicates')
  vi.spyOn(experimentsApi, 'listDesignRevisions').mockResolvedValue([])
  vi.spyOn(experimentsApi, 'listCellSummaries').mockResolvedValue([summary('a', 'a', 7893, 3946.5), summary('b', 'b', 100, 90)])
  render(<QueryClientProvider client={new QueryClient()}><CellsTab experiment={experiment} /></QueryClientProvider>)
  expect(await screen.findByText(/2 cells · 7993\/15786 replicates scored/)).toBeInTheDocument()
  expect(screen.getByText('7893/7893 replicates scored')).toBeInTheDocument()
  // Pooled from server sums: 3946.5 / 7893 and 90 / 100.
  expect(screen.getAllByText('0.5').length).toBeGreaterThan(0)
  expect(screen.getAllByText('0.9').length).toBeGreaterThan(0)
  expect(replicates).not.toHaveBeenCalled()
})
