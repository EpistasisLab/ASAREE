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
