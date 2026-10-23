import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { experimentsApi, statsApi } from '@/api/client'
import type { StatsAnalysis } from '@/types/stats'
import { StatisticsPage } from './StatisticsPage'

vi.mock('@/components/AppHeader', () => ({ AppHeader: () => null }))

const saved: StatsAnalysis = {
  id: 'analysis', backend: 'stats_sm', status: 'completed', error: null,
  created_at: '2026-10-22T18:00:00Z', fingerprint: 'input-snapshot', design_revision: 1,
  config: { metric: 'Duration', factors: [], family: 'auto', measurement_type: 'auto', denominator: null,
    ordinal_levels: [], max_order: 2, n_boot: 100, n_boot_lrt: 0, n_perm: 999, seed: 22, tweedie_power: 1.5 },
  output: { family: 'gamma', n_observations: 100, n_excluded: 0, runtime_s: 1, tables: {}, notes: [], unavailable: [],
    summary: { version: 1, metric: 'Duration', status: 'valid', description: 'Analyzed 100 runs for Duration.',
      metric_info: { description: 'Elapsed time for one run.', unit: 'seconds', coding: [] },
      findings: [], tests: [{ effect: 'Treatment', p_adjusted: .01, result: 'Evidence of an effect' }],
      effects: [{ comparison: 'Treatment: A vs B', estimate: .7, ci_low: .6, ci_high: .9,
        p_adjusted: .01, scale: 'ratio', result: 'Evidence of a difference' }],
      methods: ['Gamma model for positive durations.'], warnings: ['Independent runs assumed.'], provenance: {} } },
}

beforeEach(() => {
  vi.spyOn(experimentsApi, 'list').mockResolvedValue([{ id: 'experiment', name: 'Demo' }] as never)
  vi.spyOn(statsApi, 'history').mockResolvedValue([])
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/statistics?experiment=experiment']}>
    <StatisticsPage />
  </MemoryRouter></QueryClientProvider>)
}

it('analyzes all metrics without asking users to select statistical settings', async () => {
  const automatic = vi.spyOn(statsApi, 'automatic').mockResolvedValue([])
  mount()
  fireEvent.click(await screen.findByRole('button', { name: 'Analyze all metrics' }))
  await waitFor(() => expect(automatic).toHaveBeenCalledWith('experiment'))
  expect(screen.queryByLabelText('Model family')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Measurement type')).not.toBeInTheDocument()
})

it('shows concise statistics and a report download with no figures', async () => {
  vi.spyOn(statsApi, 'history').mockResolvedValue([saved])
  vi.spyOn(statsApi, 'get').mockResolvedValue(saved)
  mount()
  expect(await screen.findByText('Treatment: A vs B')).toBeInTheDocument()
  expect(screen.getByText('0.6 to 0.9')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Download HTML report' })).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.getByText('What ran and why')).toBeInTheDocument()
  expect(screen.getByText('Elapsed time for one run.')).toBeInTheDocument()
  expect(screen.getByText('Unit: seconds')).toBeInTheDocument()
})

it('explains why a custom metric cannot be analyzed', async () => {
  const unavailable = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!,
    status: 'unavailable' as const, tests: [], effects: [], warnings: ['Free text has no established statistical ordering.'] } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([unavailable])
  vi.spyOn(statsApi, 'get').mockResolvedValue(unavailable)
  mount()
  expect(await screen.findByText('Free text has no established statistical ordering.')).toBeInTheDocument()
  expect(screen.queryByText('Evidence of an effect')).not.toBeInTheDocument()
})

it('does not invent a definition for a metric whose description is missing', async () => {
  const missing = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!, metric_info: undefined } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([missing])
  vi.spyOn(statsApi, 'get').mockResolvedValue(missing)
  mount()
  expect(await screen.findByText('Description not provided.')).toBeInTheDocument()
})

it('shows category coding beside the metric definition', async () => {
  const coded = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!, metric_info: {
    description: 'A yes/no outcome.', unit: null, coding: ['Category coding: yes = 1; no = 0.'],
  } } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([coded])
  vi.spyOn(statsApi, 'get').mockResolvedValue(coded)
  mount()
  expect(await screen.findByText('Category coding: yes = 1; no = 0.')).toBeInTheDocument()
})
