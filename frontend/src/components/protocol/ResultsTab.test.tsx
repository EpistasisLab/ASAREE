import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { Experiment, ExperimentRunResults } from '@/types/experiments'
import { ResultsInspectorPanel, ResultsTab } from './ResultsTab'

vi.mock('@/api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/api/client')>()
  return { ...actual, experimentsApi: { ...actual.experimentsApi, getRunResults: vi.fn() } }
})

function renderWithQuery(ui: React.ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>)
}

const overview = {
  total_replicates: 1,
  completed_replicates: 1,
  running_replicates: 0,
  queued_replicates: 0,
  failed_replicates: 0,
  not_started_replicates: 0,
  obsolete_replicates: 0,
  total_cost_usd: null,
  total_input_tokens: null,
  total_output_tokens: null,
  total_tokens: null,
  total_duration_seconds: null,
  agent_run_count: 0,
  reported_usage_count: 0,
  reported_cost_count: 0,
}

const experiment = {
  id: 'experiment-1',
  name: 'Measurement UX',
  design_spec: { metrics: [] },
} as unknown as Experiment

function orderedMetricsFixture() {
  const orderedExperiment = {
    ...experiment,
    design_spec: {
      metrics: [
        { id: 'clarity', name: 'Clarity', kind: 'custom', valueType: 'number', direction: 'maximize', primary: false },
        { id: 'safety', name: 'Safety', kind: 'custom', valueType: 'number', direction: 'maximize', primary: false },
      ],
    },
    measurement_plan: {
      metrics: [
        { id: 'safety', name: 'Safety', value_type: 'number', direction: 'maximize', aggregation: 'mean', primary: false },
        { id: 'clarity', name: 'Clarity', value_type: 'number', direction: 'maximize', aggregation: 'mean', primary: false },
      ],
      producers: [],
      inputs: [],
    },
  } as unknown as Experiment
  const results = {
    overview,
    metric_keys: ['Clarity', 'Safety'],
    metric_types: { Clarity: 'number', Safety: 'number' },
    metric_aggregations: { Clarity: 'mean', Safety: 'mean' },
    metric_directions: { Clarity: 'maximize', Safety: 'maximize' },
    primary_metric: null,
    primary_metric_direction: null,
    cells: [{
      cell_label: 'model_a', factor_values: { model: 'a' }, replicate_count: 1,
      completed_count: 1, current_completed_count: 1, obsolete_count: 0,
      metric_means: { Clarity: 0.7, Safety: 0.9 }, metric_counts: { Clarity: 1, Safety: 1 },
      cost_usd: null, total_tokens: null, duration_seconds: null,
    }],
    replicates: [],
  } satisfies ExperimentRunResults
  return { orderedExperiment, results }
}

describe('results measurement states', () => {
  it('shows explicit units for cost and duration in an individual replicate result', async () => {
    const replicate = {
      replicate_label: 'replicate-1', replicate_number: 1, cell_label: 'model_a', factor_values: { model: 'a' },
      metric_values: { cost_usd: 0.0123, duration_seconds: 65 }, status: 'completed' as const, obsolete: false,
      error: null, run_id: 'run-1', protocol_revision_id: 'revision-1', updated_at: '2026-01-01T00:00:00Z',
      duration_seconds: 65, node_runs: [], input_tokens: null, output_tokens: null, total_tokens: null, cost_usd: 0.0123,
      agent_run_count: 0, reported_usage_count: 0, reported_cost_count: 0, metric_evaluation: null,
      metric_observations: [], evaluation_artifacts: [], obsolete_runs: [], superseded_runs: [],
    }
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue({
      overview,
      metric_keys: ['cost_usd', 'duration_seconds'],
      metric_types: { cost_usd: 'number', duration_seconds: 'number' },
      metric_aggregations: { cost_usd: 'sum', duration_seconds: 'sum' },
      metric_directions: { cost_usd: 'minimize', duration_seconds: 'minimize' },
      primary_metric: null,
      primary_metric_direction: null,
      cells: [],
      replicates: [replicate],
    } satisfies ExperimentRunResults)

    renderWithQuery(<ResultsInspectorPanel experimentId="experiment-1" experiment={experiment} selection={{ type: 'replicate', replicateLabel: 'replicate-1' }} onClose={vi.fn()} />)

    expect(await screen.findByText('$0.01')).toBeInTheDocument()
    expect(screen.getByText('1.1 min')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Outcome' })).not.toBeInTheDocument()
    const usage = screen.getByRole('heading', { name: 'Usage' }).parentElement!
    expect(within(usage).getAllByText(/^(Estimated cost|Duration|Total tokens|Agent calls)$/).map((label) => label.textContent)).toEqual([
      'Estimated cost',
      'Duration',
      'Total tokens',
      'Agent calls',
    ])
  })

  it('shows a measured observation once in Outcome and keeps its provenance there', async () => {
    const producer = { binding_id: 'judge', producer_id: 'asaree.judge', kind: 'deterministic_evaluator' as const, version: '1' }
    const replicate = {
      replicate_label: 'replicate-1', replicate_number: 1, cell_label: 'model_a', factor_values: { model: 'a' },
      metric_values: { Quality: 0.9 }, status: 'completed' as const, obsolete: false, error: null, run_id: 'run-1',
      protocol_revision_id: 'revision-1', updated_at: '2026-01-01T00:00:00Z', duration_seconds: null,
      node_runs: [], input_tokens: null, output_tokens: null, total_tokens: null, cost_usd: null,
      agent_run_count: 0, reported_usage_count: 0, reported_cost_count: 0, metric_evaluation: null,
      metric_observations: [{ metric_id: 'quality', metric_name: 'Quality', value_type: 'number' as const, status: 'measured' as const, value: 0.9, error: null, attempt_id: 'run-1', producer, input_provenance: {} }],
      evaluation_artifacts: [], obsolete_runs: [], superseded_runs: [],
    }
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue({
      overview,
      metric_keys: ['Quality'], metric_types: { Quality: 'number' }, metric_aggregations: { Quality: 'mean' },
      metric_directions: { Quality: 'maximize' }, primary_metric: null, primary_metric_direction: null,
      cells: [], replicates: [replicate],
    } satisfies ExperimentRunResults)

    renderWithQuery(<ResultsInspectorPanel experimentId="experiment-1" experiment={experiment} selection={{ type: 'replicate', replicateLabel: 'replicate-1' }} onClose={vi.fn()} />)

    expect(await screen.findByRole('heading', { name: 'Outcome' })).toBeInTheDocument()
    expect(screen.getAllByText('0.9')).toHaveLength(1)
    expect(screen.getByText('asaree.judge · v1')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Measurement observations' })).not.toBeInTheDocument()
  })

  it('presents metrics in the measurement-plan order declared by the designer', async () => {
    const { orderedExperiment, results } = orderedMetricsFixture()
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsTab experimentId="experiment-1" experimentName="Measurement UX" experiment={orderedExperiment} onSelectResult={vi.fn()} />)

    expect(await screen.findByRole('combobox', { name: 'Choose comparison metric' })).toHaveTextContent('Safety')
    expect(screen.getAllByText(/Safety/).length).toBeGreaterThan(0)
  })

  it('uses measurement-plan order in the separate cell result details', async () => {
    const { orderedExperiment, results } = orderedMetricsFixture()
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsInspectorPanel experimentId="experiment-1" experiment={orderedExperiment} selection={{ type: 'cell', cellLabel: 'model_a' }} onClose={vi.fn()} />)

    const summary = await screen.findByRole('region', { name: 'Cell result summary' })
    expect(within(summary).getAllByText(/^(Safety|Clarity) · average$/).map((label) => label.textContent)).toEqual([
      'Safety · average',
      'Clarity · average',
    ])
  })

  it('shows condition-level cost and duration once under Usage', async () => {
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue({
      overview,
      metric_keys: ['cost_usd', 'duration_seconds'],
      metric_types: { cost_usd: 'number', duration_seconds: 'number' },
      metric_aggregations: { cost_usd: 'sum', duration_seconds: 'sum' },
      metric_directions: { cost_usd: 'minimize', duration_seconds: 'minimize' },
      primary_metric: null,
      primary_metric_direction: null,
      cells: [{
        cell_label: 'model_a', factor_values: { model: 'a' }, replicate_count: 2, completed_count: 2,
        current_completed_count: 2, obsolete_count: 0, metric_means: { cost_usd: 0.0123, duration_seconds: 65 },
        metric_counts: { cost_usd: 2, duration_seconds: 2 }, cost_usd: 0.0123, total_tokens: null, duration_seconds: 65,
      }],
      replicates: [],
    } satisfies ExperimentRunResults)

    renderWithQuery(<ResultsInspectorPanel experimentId="experiment-1" experiment={experiment} selection={{ type: 'cell', cellLabel: 'model_a' }} onClose={vi.fn()} />)

    expect(await screen.findByText('$0.01')).toBeInTheDocument()
    expect(screen.getByText('1.1 min')).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Outcome metrics' })).not.toBeInTheDocument()
  })

  it('ranks measured minimize values before missing values without skipping rank one', async () => {
    const results = {
      overview: { ...overview, total_replicates: 2, completed_replicates: 2 },
      metric_keys: ['loss'],
      metric_types: { loss: 'number' },
      metric_aggregations: { loss: 'mean' },
      metric_directions: { loss: 'minimize' },
      primary_metric: 'loss',
      primary_metric_direction: 'minimize',
      cells: [
        { cell_label: 'missing', factor_values: { model: 'missing' }, replicate_count: 1, completed_count: 1, current_completed_count: 1, obsolete_count: 0, metric_means: {} as Record<string, number>, metric_counts: {} as Record<string, number>, cost_usd: null, total_tokens: null, duration_seconds: null },
        { cell_label: 'measured', factor_values: { model: 'measured' }, replicate_count: 1, completed_count: 1, current_completed_count: 1, obsolete_count: 0, metric_means: { loss: 0.2 }, metric_counts: { loss: 1 }, cost_usd: null, total_tokens: null, duration_seconds: null },
      ],
      replicates: [],
    } satisfies ExperimentRunResults
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsTab experimentId="experiment-1" experimentName="Measurement UX" experiment={experiment} onSelectResult={vi.fn()} />)

    const rows = await screen.findAllByRole('row')
    expect(rows[1]).toHaveTextContent('model: measured')
    expect(rows[1].firstElementChild).toHaveTextContent('1')
    expect(rows[2]).toHaveTextContent('No measured observations')
  })

  it('summarizes unavailable condition observations instead of collapsing them into a blank', async () => {
    const producer = { binding_id: 'python', producer_id: 'asaree.python_script', kind: 'reported' as const, version: '1' }
    const results = {
      overview,
      metric_keys: ['ROC-AUC'],
      metric_types: { 'ROC-AUC': 'number' },
      metric_aggregations: { 'ROC-AUC': 'mean' },
      metric_directions: { 'ROC-AUC': 'maximize' },
      primary_metric: 'ROC-AUC',
      primary_metric_direction: 'maximize',
      cells: [{ cell_label: 'model_a', factor_values: { model: 'a' }, replicate_count: 2, completed_count: 2, current_completed_count: 2, obsolete_count: 0, metric_means: { 'ROC-AUC': 0.8 }, metric_counts: { 'ROC-AUC': 1 }, cost_usd: null, total_tokens: null, duration_seconds: null }],
      replicates: [{
        replicate_label: 'replicate-1', replicate_number: 1, cell_label: 'model_a', factor_values: { model: 'a' }, metric_values: {}, status: 'completed' as const, obsolete: false, error: null, run_id: 'run-1', protocol_revision_id: 'revision-1', updated_at: '2026-01-01T00:00:00Z', duration_seconds: null, node_runs: [], input_tokens: null, output_tokens: null, total_tokens: null, cost_usd: null, agent_run_count: 0, reported_usage_count: 0, reported_cost_count: 0, metric_evaluation: null,
        metric_observations: [{ metric_id: 'auc', metric_name: 'ROC-AUC', value_type: 'number' as const, status: 'unavailable' as const, value: null, error: 'Probability output is missing.', attempt_id: 'run-1', producer, input_provenance: {} }],
        evaluation_artifacts: [], obsolete_runs: [], superseded_runs: [],
      }],
    } satisfies ExperimentRunResults
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsTab experimentId="experiment-1" experimentName="Measurement UX" experiment={experiment} onSelectResult={vi.fn()} />)

    expect(await screen.findByText('Unavailable (1)')).toBeInTheDocument()
  })

  it('keeps a neutral scalar comparable without declaring a winner', async () => {
    const results = {
      overview,
      metric_keys: ['variance'],
      metric_types: { variance: 'number' },
      metric_aggregations: { variance: 'mean' },
      metric_directions: { variance: 'neutral' },
      primary_metric: null,
      primary_metric_direction: null,
      cells: [{
        cell_label: 'model_a', factor_values: { model: 'a' }, replicate_count: 1,
        completed_count: 1, current_completed_count: 1, obsolete_count: 0,
        metric_means: { variance: 0.2 }, metric_counts: { variance: 1 },
        cost_usd: null, total_tokens: null, duration_seconds: null,
      }],
      replicates: [],
    } satisfies ExperimentRunResults
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsTab experimentId="experiment-1" experimentName="Measurement UX" experiment={experiment} onSelectResult={vi.fn()} />)

    expect(await screen.findByText('Comparison only')).toBeInTheDocument()
    expect(screen.getByText(/No primary metric is declared/)).toBeInTheDocument()
    expect(screen.queryByText('Best current result')).not.toBeInTheDocument()
    expect(screen.getAllByText('0.2').length).toBeGreaterThan(0)
  })

  it('compares directional metrics without ranking or naming a winner when no primary is declared', async () => {
    const results = {
      overview: { ...overview, total_replicates: 2, completed_replicates: 2 },
      metric_keys: ['quality'],
      metric_types: { quality: 'number' },
      metric_aggregations: { quality: 'mean' },
      metric_directions: { quality: 'maximize' },
      primary_metric: null,
      primary_metric_direction: null,
      cells: [
        { cell_label: 'b', factor_values: { model: 'b' }, replicate_count: 1, completed_count: 1, current_completed_count: 1, obsolete_count: 0, metric_means: { quality: 0.9 }, metric_counts: { quality: 1 }, cost_usd: null, total_tokens: null, duration_seconds: null },
        { cell_label: 'a', factor_values: { model: 'a' }, replicate_count: 1, completed_count: 1, current_completed_count: 1, obsolete_count: 0, metric_means: { quality: 0.2 }, metric_counts: { quality: 1 }, cost_usd: null, total_tokens: null, duration_seconds: null },
      ],
      replicates: [],
    } satisfies ExperimentRunResults
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsTab experimentId="experiment-1" experimentName="Measurement UX" experiment={experiment} onSelectResult={vi.fn()} />)

    expect(await screen.findByText('Comparison only')).toBeInTheDocument()
    expect(screen.getByText(/No primary metric is declared/)).toBeInTheDocument()
    expect(screen.queryByText('Best current result')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Cell comparison/ })).toBeInTheDocument()
    const rows = screen.getAllByRole('row')
    expect(rows[1]).toHaveTextContent('model: a')
    expect(rows[1].firstElementChild).toHaveTextContent('—')
  })

  it('does not rank an inspected metric that is not the declared primary', async () => {
    const user = userEvent.setup()
    const results = {
      overview: { ...overview, total_replicates: 2, completed_replicates: 2 },
      metric_keys: ['loss', 'quality'],
      metric_types: { loss: 'number', quality: 'number' },
      metric_aggregations: { loss: 'mean', quality: 'mean' },
      metric_directions: { loss: 'minimize', quality: 'maximize' },
      primary_metric: 'loss',
      primary_metric_direction: 'minimize',
      cells: [
        { cell_label: 'a', factor_values: { model: 'a' }, replicate_count: 1, completed_count: 1, current_completed_count: 1, obsolete_count: 0, metric_means: { loss: 0.8, quality: 0.9 }, metric_counts: { loss: 1, quality: 1 }, cost_usd: null, total_tokens: null, duration_seconds: null },
        { cell_label: 'b', factor_values: { model: 'b' }, replicate_count: 1, completed_count: 1, current_completed_count: 1, obsolete_count: 0, metric_means: { loss: 0.2, quality: 0.2 }, metric_counts: { loss: 1, quality: 1 }, cost_usd: null, total_tokens: null, duration_seconds: null },
      ],
      replicates: [],
    } satisfies ExperimentRunResults
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsTab experimentId="experiment-1" experimentName="Measurement UX" experiment={experiment} onSelectResult={vi.fn()} />)
    await screen.findByText('Best current result')
    await user.click(screen.getByRole('combobox', { name: 'Choose comparison metric' }))
    await user.click(screen.getByRole('option', { name: 'quality' }))

    expect(screen.getByText('Comparison only')).toBeInTheDocument()
    expect(screen.getByText(/quality is not the declared primary metric/)).toBeInTheDocument()
    expect(screen.queryByText('Best current result')).not.toBeInTheDocument()
    expect(screen.getByRole('heading', { name: /Cell comparison/ })).toBeInTheDocument()
  })

  it('renders unavailable, failed, and not-applicable observations plus diagnostic artifacts', async () => {
    const producer = { binding_id: 'python', producer_id: 'asaree.python_script', kind: 'reported' as const, version: '1' }
    const replicate = {
      replicate_label: 'replicate-1', replicate_number: 1, cell_label: 'model_a', factor_values: { model: 'a' },
      metric_values: {}, status: 'completed' as const, obsolete: false, error: null, run_id: 'run-1',
      protocol_revision_id: 'revision-1', updated_at: '2026-01-01T00:00:00Z', duration_seconds: 1,
      node_runs: [], input_tokens: null, output_tokens: null, total_tokens: null, cost_usd: null,
      agent_run_count: 0, reported_usage_count: 0, reported_cost_count: 0, metric_evaluation: null,
      metric_observations: [
        { metric_id: 'auc', metric_name: 'ROC-AUC', value_type: 'number' as const, status: 'unavailable' as const, value: null, error: 'Probability output is missing.', attempt_id: 'run-1', producer, input_provenance: {} },
        { metric_id: 'loss', metric_name: 'Log loss', value_type: 'number' as const, status: 'failed' as const, value: null, error: 'Evaluator crashed.', attempt_id: 'run-1', producer, input_provenance: {} },
        { metric_id: 'mcc', metric_name: 'MCC', value_type: 'number' as const, status: 'not_applicable' as const, value: null, error: 'Regression task.', attempt_id: 'run-1', producer, input_provenance: {} },
      ],
      evaluation_artifacts: [
        { artifact_key: 'confusion_matrix', kind: 'confusion_matrix', payload: { labels: ['no', 'yes'], matrix: [[8, 1], [2, 9]] }, attempt_id: 'run-1', producer, input_provenance: {} },
        { artifact_key: 'calibration', kind: 'calibration', payload: { probability: [0.2], observed: [0.1] }, attempt_id: 'run-1', producer, input_provenance: {} },
        { artifact_key: 'per_class', kind: 'per_class', payload: [{ label: 'yes', precision: 0.9, recall: 0.82, f1: 0.86, support: 11 }], attempt_id: 'run-1', producer, input_provenance: {} },
      ],
      obsolete_runs: [], superseded_runs: [],
    }
    const results = {
      overview,
      metric_keys: [], metric_types: {}, metric_aggregations: {}, metric_directions: {},
      primary_metric: null, primary_metric_direction: null,
      cells: [], replicates: [replicate],
    } satisfies ExperimentRunResults
    vi.mocked(experimentsApi.getRunResults).mockResolvedValue(results)

    renderWithQuery(<ResultsInspectorPanel experimentId="experiment-1" experiment={experiment} selection={{ type: 'replicate', replicateLabel: 'replicate-1' }} onClose={vi.fn()} />)

    expect(await screen.findByText('Unavailable')).toBeInTheDocument()
    expect(screen.getByText('Probability output is missing.')).toBeInTheDocument()
    expect(screen.getByText('Failed')).toBeInTheDocument()
    expect(screen.getByText('Evaluator crashed.')).toBeInTheDocument()
    expect(screen.getByText('Not applicable')).toBeInTheDocument()
    expect(screen.getByText('Regression task.')).toBeInTheDocument()
    expect(screen.getByText('Confusion matrix')).toBeInTheDocument()
    expect(screen.getByText('Calibration')).toBeInTheDocument()
    expect(screen.getByText('Per-class report')).toBeInTheDocument()
  })
})
