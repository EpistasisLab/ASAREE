import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { Experiment, ExperimentRunResults, Replicate } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunsTab } from './RunsTab'

describe('RunsTab factor discrepancy warning', () => {
  afterEach(() => vi.restoreAllMocks())

  it('warns immediately and blocks runs when the canvas disagrees with generated factor levels', async () => {
    vi.spyOn(experimentsApi, 'getExecutionFactorOptions').mockResolvedValue({ factors: {}, truncated: false })
    vi.spyOn(experimentsApi, 'getExecutionSummary').mockResolvedValue({ ready: true, metric_types: {}, metric_directions: {} } as never)
    vi.spyOn(experimentsApi, 'listExecutionCells').mockResolvedValue({ items: [], next_cursor: null })
    vi.spyOn(experimentsApi, 'listReplicates').mockResolvedValue([{
      id: 'replicate-1', cell_id: 'cell-1', cell_label: 'Prompt:original', replicate_label: 'Prompt:original__rep1',
      replicate_number: 1, design_revision_id: 'design-1', run_id: null, workspace_id: null,
      factor_values: { Prompt: 'original prompt' }, metric_values: null, artifacts: null, created_at: '', updated_at: '',
    } satisfies Replicate])
    vi.spyOn(experimentsApi, 'listTrials').mockResolvedValue([])
    vi.spyOn(experimentsApi, 'getRunResults').mockResolvedValue({
      overview: { total_cost_usd: null, total_tokens: null, total_duration_seconds: null },
      cells: [], replicates: [], metric_keys: [], metric_types: {}, metric_aggregations: {}, metric_directions: {},
      primary_metric: null, primary_metric_direction: null,
    } as unknown as ExperimentRunResults)
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({ id: 'experiment-1' } as unknown as Experiment)

    const protocol = {
      id: 'protocol-1', published_revision_id: 'revision-2', published_revision: 2, has_unpublished_changes: false,
      graph: {
        nodes: [{
          id: 'agent-1', type: 'agent', position: { x: 0, y: 0 },
          data: { label: 'Writer', factor_bindings: { 'config.system_prompt': 'Prompt' }, config: { system_prompt: 'newly published prompt' } },
        }],
        edges: [],
      },
    } as unknown as Protocol
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

    render(
      <QueryClientProvider client={client}>
        <RunsTab
          experimentId="experiment-1"
          designSpec={{ factors: [{ name: 'Prompt', levels: ['original prompt', 'alternate prompt'] }] }}
          protocol={protocol}
          regenerationRequired={false}
          unboundFactors={[]}
          onViewResult={vi.fn()}
        />
      </QueryClientProvider>,
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('Canvas and generated runs disagree')
    expect(screen.getByRole('button', { name: 'Run all cells' })).toBeDisabled()
  })
})

describe('RunsTab stop controls', () => {
  afterEach(() => vi.restoreAllMocks())
  const protocol = { id: 'protocol-1', published_revision_id: 'revision-1', published_revision: 1, has_unpublished_changes: false,
    graph: { nodes: [], edges: [] } } as unknown as Protocol
  const cell = (counts: Record<string, number>) => ({ cell_id: 'cell-1', cell_label: 'model=a', factor_values: { model: 'a' },
    replicate_count: 3, total: 3, counts })

  async function mount(hasActive: boolean, counts: Record<string, number>, summaryCounts = counts, total = 3) {
    vi.spyOn(experimentsApi, 'getExecutionFactorOptions').mockResolvedValue({ factors: {}, truncated: false })
    vi.spyOn(experimentsApi, 'getExecutionSummary').mockResolvedValue(
      { ready: true, has_active: hasActive, counts: summaryCounts, total, metric_types: {}, metric_directions: {} } as never)
    vi.spyOn(experimentsApi, 'listExecutionCells').mockResolvedValue({ items: [cell(counts)], next_cursor: null } as never)
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunsTab experimentId="experiment-1" designSpec={{}} protocol={protocol} regenerationRequired={false} unboundFactors={[]} onViewResult={vi.fn()} />
    </QueryClientProvider>)
    await screen.findByRole('button', { name: /^(Re-run|Run) cell$/ })
  }

  it('hides Stop all and Stop cell when nothing is pending or running', async () => {
    await mount(false, { completed: 2, failed: 1 })
    expect(screen.queryByRole('button', { name: 'Stop all' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop cell' })).not.toBeInTheDocument()
  })

  it('offers Stop all and Stop cell while executions are active', async () => {
    await mount(true, { completed: 1, running: 1, pending: 1 })
    expect(await screen.findByRole('button', { name: 'Stop all' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Stop cell' })).toBeInTheDocument()
  })

  it.each([
    [{ completed: 3 }, 'Re-run cell'],
    [{ completed: 2, failed: 1 }, 'Re-run cell'],
    [{ failed: 1, cancelled: 1, limit_reached: 1 }, 'Re-run cell'],
    [{ completed: 2, not_started: 1 }, 'Run cell'],
    [{ completed: 2, running: 1 }, 'Run cell'],
    [{ completed: 2, pending: 1 }, 'Run cell'],
    [{ completed: 2, finalizing: 1 }, 'Run cell'],
    [{ not_started: 3 }, 'Run cell'],
  ] as const)('labels the cell action for %j as %s', async (counts, label) => {
    await mount(false, counts)
    expect(screen.getByRole('button', { name: label })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: label === 'Re-run cell' ? 'Re-run all cells' : 'Run all cells' })).toBeInTheDocument()
  })

  it('keeps Run all cells when another cell still has unstarted replicates', async () => {
    await mount(false, { completed: 3 }, { completed: 3, not_started: 3 }, 6)
    expect(screen.getByRole('button', { name: 'Re-run cell' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Run all cells' })).toBeInTheDocument()
  })

  it('uses the whole design summary when other finished cells are on another page', async () => {
    await mount(false, { completed: 3 }, { completed: 4, failed: 2 }, 6)
    expect(screen.getByRole('button', { name: 'Re-run all cells' })).toBeInTheDocument()
  })
})
