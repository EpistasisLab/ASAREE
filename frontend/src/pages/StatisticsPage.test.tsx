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
      findings: [], tests: [{ effect: 'Treatment', p_adjusted: .01, result: 'Evidence of an effect',
        method: 'Wald chi-square test (HC0 robust covariance)' }],
      effects: [{ comparison: 'Treatment: A vs B', estimate: .7, ci_low: .6, ci_high: .9,
        p_adjusted: .01, scale: 'ratio', result: 'Evidence of a difference',
        method: 'Delta-method Wald z-test (HC0 robust covariance)' }],
      methods: ['Gamma model for positive durations.',
        'Factor tests — Wald chi-square test: checks whether the factor contributes to the fitted model.'],
      warnings: ['Independent runs assumed.'], provenance: {} } },
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
  expect(screen.getByText('Wald chi-square test (HC0 robust covariance)')).toBeInTheDocument()
  expect(screen.getByText('Delta-method Wald z-test (HC0 robust covariance)')).toBeInTheDocument()
  expect(screen.getByText(/checks whether the factor contributes to the fitted model/)).toBeInTheDocument()
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

it('presents both inference methods with counts and unavailable checks explicitly', async () => {
  const both: StatsAnalysis = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!,
    resampling: { bootstrap: { requested: 1000, attempted: 1000, successful: 990, failed: 10, status: 'completed' }, permutations: [] },
    tests: [{ ...saved.output!.summary!.tests[0], permutation_p_adjusted: .08,
      permutation_status: 'completed', permutation_result: 'No clear evidence in sensitivity check' }],
    effects: [{ ...saved.output!.summary!.effects[0], bootstrap_ci_low: .58, bootstrap_ci_high: 1.02,
      bootstrap_status: 'completed', interval_agreement: 'Intervals differ; investigate' }],
  } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([both])
  vi.spyOn(statsApi, 'get').mockResolvedValue(both)
  mount()
  expect(await screen.findAllByText('Analytic adjusted p', { selector: 'th' })).toHaveLength(2)
  expect(screen.getByText('Permutation adjusted p')).toBeInTheDocument()
  expect(screen.getByText('0.080')).toBeInTheDocument()
  expect(screen.getByText('No clear evidence in sensitivity check')).toBeInTheDocument()
  expect(screen.getByText('Bootstrap 95% interval')).toBeInTheDocument()
  expect(screen.getByText('0.58 to 1.02')).toBeInTheDocument()
  expect(screen.getByText('Intervals differ; investigate')).toBeInTheDocument()
  expect(screen.getByText(/990\/1000 successful refits; 10 failed/)).toBeInTheDocument()
})

it('keeps analytic results visible when resampling is unavailable', async () => {
  const incomplete: StatsAnalysis = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!,
    resampling: { bootstrap: { requested: 1000, attempted: 20, successful: 0, failed: 20, status: 'Too many failed refits' }, permutations: [] },
    tests: [{ ...saved.output!.summary!.tests[0], permutation_p_adjusted: null,
      permutation_status: 'Unequal trial totals prevent this permutation check.' }],
    effects: [{ ...saved.output!.summary!.effects[0], bootstrap_ci_low: null, bootstrap_ci_high: null,
      bootstrap_status: 'Too many failed refits' }],
  } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([incomplete])
  vi.spyOn(statsApi, 'get').mockResolvedValue(incomplete)
  mount()
  expect(await screen.findByText('Unequal trial totals prevent this permutation check.')).toBeInTheDocument()
  expect(screen.getByText('0.6 to 0.9')).toBeInTheDocument()
  expect(screen.getByText('Too many failed refits')).toBeInTheDocument()
  expect(screen.getAllByText('Unavailable')).toHaveLength(2)
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
