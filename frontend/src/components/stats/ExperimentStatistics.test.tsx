import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { statsApi } from '@/api/client'
import type { StatsAnalysis } from '@/types/stats'
import { ExperimentStatistics } from './ExperimentStatistics'

vi.mock('@/components/AppHeader', () => ({ AppHeader: () => null }))

const saved: StatsAnalysis = {
  id: 'analysis', backend: 'stats_sm', status: 'completed', report_status: 'valid', error: null,
  created_at: '2026-10-22T18:00:00Z', fingerprint: 'input-snapshot', design_revision: 1,
  config: { metric: 'Duration', factors: [], family: 'auto', measurement_type: 'auto', denominator: null,
    ordinal_levels: [], max_order: 2, n_boot: 100, n_boot_lrt: 0, n_perm: 999, seed: 22, tweedie_power: 1.5 },
  output: { family: 'gamma', n_observations: 100, n_excluded: 0, runtime_s: 1, tables: {}, notes: [], unavailable: [],
    summary: { version: 1, metric: 'Duration', status: 'valid', description: 'Analyzed 100 runs for Duration.',
      distribution: { n: 100, minimum: 1, maximum: 9, mean: 4, sd: 2, distinct: 20, zeros: 0,
        plot: { kind: 'histogram', bins: [{ label: '1–5', count: 60 }, { label: '5–9', count: 40 }] } },
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
  vi.spyOn(statsApi, 'history').mockResolvedValue([])
  vi.spyOn(statsApi, 'source').mockResolvedValue({
    factors: [], metrics: [], n_replicates: 10, n_measured_replicates: 10,
    design_revision: 1, fingerprint: 'source', excluded_row_replicates: 0,
  })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount(version: string | null = 'version-1') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/statistics?experiment=experiment']}>
    <ExperimentStatistics experiment="experiment" version={version} />
  </MemoryRouter></QueryClientProvider>)
}

it('analyzes all metrics without asking users to select statistical settings', async () => {
  const automatic = vi.spyOn(statsApi, 'automatic').mockResolvedValue([])
  mount()
  await waitFor(() => expect(screen.getByRole('button', { name: 'Analyze all metrics' })).toBeEnabled())
  fireEvent.click(await screen.findByRole('button', { name: 'Analyze all metrics' }))
  await waitFor(() => expect(automatic).toHaveBeenCalledWith('experiment', 'version-1'))
  expect(screen.queryByLabelText('Model family')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Measurement type')).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Experiment')).not.toBeInTheDocument()
  expect(statsApi.source).toHaveBeenCalledWith('experiment', 'version-1')
  expect(statsApi.history).toHaveBeenCalledWith('experiment', 'stats_sm', 'version-1')
})

it('disables analysis when this version has no completed measured replicates', async () => {
  vi.mocked(statsApi.source).mockResolvedValue({
    factors: [], metrics: [], n_replicates: 10, n_measured_replicates: 0,
    design_revision: 1, fingerprint: 'empty', excluded_row_replicates: 0,
  })
  const automatic = vi.spyOn(statsApi, 'automatic')
  mount('older-version')
  expect(await screen.findByText(/Run replicates for this experiment version/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Analyze all metrics' })).toBeDisabled()
  expect(statsApi.source).toHaveBeenCalledWith('experiment', 'older-version')
  expect(statsApi.history).toHaveBeenCalledWith('experiment', 'stats_sm', 'older-version')
  fireEvent.click(screen.getByRole('button', { name: 'Analyze all metrics' }))
  expect(automatic).not.toHaveBeenCalled()
})

it('requires a publication before enabling statistics', () => {
  mount(null)
  expect(screen.getByText(/Publish the experiment and run replicates/)).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Analyze all metrics' })).toBeDisabled()
  expect(statsApi.source).not.toHaveBeenCalled()
  expect(statsApi.history).not.toHaveBeenCalled()
})

it('shows concise statistics and a report download', async () => {
  vi.spyOn(statsApi, 'history').mockResolvedValue([saved])
  vi.spyOn(statsApi, 'get').mockResolvedValue(saved)
  mount()
  expect(await screen.findByRole('button', { name: /Duration/ })).toHaveAttribute('aria-expanded', 'false')
  expect(statsApi.get).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: /Duration/ }))
  expect(await screen.findByText('Treatment: A vs B')).toBeInTheDocument()
  expect(screen.getByText('0.6 to 0.9')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Download HTML report' })).toBeInTheDocument()
  expect(screen.queryByRole('img')).not.toBeInTheDocument()
  expect(screen.getByText('What ran and why')).toBeInTheDocument()
  expect(screen.getByText('Elapsed time for one run.')).toBeInTheDocument()
  expect(screen.getByText('Unit: seconds')).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: 'What ran and why' })).getByText('Wald chi-square test (HC0 robust covariance)')).toBeInTheDocument()
  expect(within(screen.getByRole('region', { name: 'What ran and why' })).getByText('Delta-method Wald z-test (HC0 robust covariance)')).toBeInTheDocument()
  expect(screen.getByText(/checks whether the factor contributes to the fitted model/)).toBeInTheDocument()
})

it('hides reports without valid conclusions', async () => {
  const unavailable = { ...saved, report_status: 'unavailable' as const, output: { ...saved.output!, summary: { ...saved.output!.summary!,
    status: 'unavailable' as const, tests: [], effects: [], warnings: ['Free text has no established statistical ordering.'] } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([unavailable, { ...unavailable, id: 'unreliable', report_status: 'unreliable' }])
  vi.spyOn(statsApi, 'get').mockResolvedValue(unavailable)
  mount()
  expect(await screen.findByText(/No statistical reports are available/)).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Duration/ })).not.toBeInTheDocument()
  expect(statsApi.get).not.toHaveBeenCalled()
  expect(screen.queryByText('Evidence of an effect')).not.toBeInTheDocument()
})

it('shows only the latest report for each metric while older duplicates are being removed', async () => {
  const older = { ...saved, id: 'older-report', created_at: '2026-10-21T18:00:00Z' }
  vi.spyOn(statsApi, 'history').mockResolvedValue([saved, older])
  vi.spyOn(statsApi, 'get').mockResolvedValue(saved)
  mount()
  expect(await screen.findAllByRole('button', { name: /Duration/ })).toHaveLength(1)
  fireEvent.click(screen.getByRole('button', { name: /Duration/ }))
  await waitFor(() => expect(statsApi.get).toHaveBeenCalledWith('experiment', 'analysis'))
})

it('replaces old stored narrative with conversational summaries of the numeric tests', async () => {
  const findings = [
    'Both the main test and the permutation check found evidence that Effort has an effect on this metric.',
    'Both the main test and the permutation check found evidence that Feature budget has an effect on this metric.',
    'The main test found no clear evidence that the effect of Effort changes across Feature budget settings. A permutation check was not available for this comparison.',
  ]
  const old = 'Effort: analytic — evidence of an effect; permutation — evidence in sensitivity check.'
  const tests = [
    { ...saved.output!.summary!.tests[0], effect: 'Effort', p_adjusted: .01, permutation_p_adjusted: .02 },
    { ...saved.output!.summary!.tests[0], effect: 'Feature budget', p_adjusted: .01, permutation_p_adjusted: .02 },
    { ...saved.output!.summary!.tests[0], effect: 'Effort:Feature budget', p_adjusted: .4, permutation_p_adjusted: null },
  ]
  const analysis = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!, findings: [old], tests } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([analysis])
  vi.spyOn(statsApi, 'get').mockResolvedValue(analysis)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  const list = within(await screen.findByRole('list', { name: 'Key findings' }))
  expect(list.getAllByRole('listitem')).toHaveLength(3)
  for (const finding of findings) expect(list.getByText(finding)).toBeVisible()
  expect(screen.queryByText(old)).not.toBeInTheDocument()
  expect(screen.getByText(/Explanations use fixed templates; no AI is used/)).toBeInTheDocument()
})

it('surfaces the selected distribution and saved observed summaries', async () => {
  vi.spyOn(statsApi, 'history').mockResolvedValue([saved])
  vi.spyOn(statsApi, 'get').mockResolvedValue(saved)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  const section = within(await screen.findByRole('region', { name: 'Metric distribution' }))
  expect(screen.getAllByText('Gamma')[0]).toBeVisible()
  for (const [label, number] of [
    ['Replicates', '100'], ['Minimum', '1'], ['Maximum', '9'], ['Mean', '4'],
    ['Standard deviation', '2'], ['Distinct values', '20'], ['Zero values', '0'],
  ]) {
    expect(section.getByText(label, { selector: 'dt' }).nextElementSibling).toHaveTextContent(number)
  }
  expect(section.getByText(/accounting for the experimental factors/)).toBeVisible()
  const chart = within(section.getByRole('figure', { name: 'Duration observed distribution histogram' }))
  expect(chart.getByTitle('1–5: 60 replicates')).toBeVisible()
  expect(chart.getByTitle('5–9: 40 replicates')).toBeVisible()
})

it('renders histogram bars even when a saved plot keeps raw values', async () => {
  const plotted: StatsAnalysis = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!,
    distribution: { ...saved.output!.summary!.distribution!,
      plot: { kind: 'histogram',
        bins: [{ label: '1–5', count: 3 }, { label: '5–9', count: 2 }],
        values: [1, 2, 2, 6, 9] } } } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([plotted])
  vi.spyOn(statsApi, 'get').mockResolvedValue(plotted)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  const chart = within(await screen.findByRole('figure', { name: 'Duration observed distribution histogram' }))
  expect(chart.queryByText('Each dot is one replicate')).not.toBeInTheDocument()
  expect(chart.getByTitle('1–5: 3 replicates')).toBeVisible()
  expect(chart.getByTitle('5–9: 2 replicates')).toBeVisible()
  expect(chart.getByText('Metric value (seconds)')).toBeInTheDocument()
})

it('hides the distribution section when no saved observations or summaries exist', async () => {
  const older = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!, distribution: undefined } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([older])
  vi.spyOn(statsApi, 'get').mockResolvedValue(older)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  expect((await screen.findAllByText('Gamma')).length).toBeGreaterThan(0)
  expect(screen.queryByRole('region', { name: 'Metric distribution' })).not.toBeInTheDocument()
  expect(screen.getByText('Treatment: A vs B')).toBeVisible()
})

it('explains missing observations instead of displaying an empty chart', async () => {
  const older = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!,
    distribution: { ...saved.output!.summary!.distribution!, plot: null },
  } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([older])
  vi.spyOn(statsApi, 'get').mockResolvedValue(older)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  expect(await screen.findByText(/does not contain enough observations to draw a chart/)).toBeVisible()
  expect(screen.queryByRole('figure')).not.toBeInTheDocument()
})

it('organizes methods into an overview with optional full technical details', async () => {
  vi.spyOn(statsApi, 'history').mockResolvedValue([saved])
  vi.spyOn(statsApi, 'get').mockResolvedValue(saved)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  const overview = within(await screen.findByRole('region', { name: 'What ran and why' }))
  for (const title of ['Regression Model Selection', 'Tests and comparisons', 'Uncertainty checks', 'How to interpret it']) {
    expect(overview.getByRole('heading', { name: title })).toBeVisible()
  }
  const disclosure = overview.getByText('Full technical details').closest('details')!
  expect(disclosure).not.toHaveAttribute('open')
  fireEvent.click(overview.getByText('Full technical details'))
  expect(disclosure).toHaveAttribute('open')
  expect(overview.getByText('input-snapshot')).toBeVisible()
})

it('lists reports independently and loads them only when expanded', async () => {
  const other = { ...saved, id: 'other', config: { ...saved.config, metric: 'Success' } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([saved, other])
  const get = vi.spyOn(statsApi, 'get').mockResolvedValue(saved)
  mount()
  const duration = await screen.findByRole('button', { name: /Duration/ })
  const success = screen.getByRole('button', { name: /Success/ })
  expect(get).not.toHaveBeenCalled()
  fireEvent.click(duration)
  expect(await screen.findByText('Treatment: A vs B')).toBeVisible()
  expect(success).toHaveAttribute('aria-expanded', 'false')
  expect(get).toHaveBeenCalledTimes(1)
  fireEvent.click(duration)
  expect(duration).toHaveAttribute('aria-expanded', 'false')
  expect(screen.queryByText('Treatment: A vs B')).not.toBeInTheDocument()
  fireEvent.click(success)
  await waitFor(() => expect(get).toHaveBeenCalledWith('experiment', 'other'))
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
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
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
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
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
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  expect(await screen.findByText('Description not provided.')).toBeInTheDocument()
})

it('shows category coding beside the metric definition', async () => {
  const coded = { ...saved, output: { ...saved.output!, summary: { ...saved.output!.summary!, metric_info: {
    description: 'A yes/no outcome.', unit: null, coding: ['Category coding: yes = 1; no = 0.'],
  } } } }
  vi.spyOn(statsApi, 'history').mockResolvedValue([coded])
  vi.spyOn(statsApi, 'get').mockResolvedValue(coded)
  mount()
  fireEvent.click(await screen.findByRole('button', { name: /Duration/ }))
  expect(await screen.findByText('Category coding: yes = 1; no = 0.')).toBeInTheDocument()
})
