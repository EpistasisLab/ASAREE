import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Experiment, ExperimentRunResults, Replicate } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { RunsTab } from './RunsTab'

describe('RunsTab factor discrepancy warning', () => {
  afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })

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
    expect(screen.getByRole('button', { name: 'Run pending' })).toBeDisabled()
  })
})

describe('RunsTab stop controls', () => {
  afterEach(() => { vi.restoreAllMocks(); sessionStorage.clear() })
  const protocol = { id: 'protocol-1', published_revision_id: 'revision-1', published_revision: 1, has_unpublished_changes: false,
    graph: { nodes: [], edges: [] } } as unknown as Protocol
  const cell = (counts: Record<string, number>) => ({ cell_id: 'cell-1', cell_label: 'model=a', factor_values: { model: 'a' },
    replicate_count: 3, total: 3, counts })

  async function mount(hasActive: boolean, counts: Record<string, number>, rowMode = false, unpublished = false) {
    vi.spyOn(experimentsApi, 'getExecutionFactorOptions').mockResolvedValue({ factors: { model: ['a', 'b'] }, truncated: false })
    vi.spyOn(experimentsApi, 'getExecutionSummary').mockResolvedValue(
      { ready: true, unit: rowMode ? 'row' : 'replicate', has_active: hasActive, counts, metric_types: {}, metric_directions: {} } as never)
    vi.spyOn(experimentsApi, 'listExecutionCells').mockResolvedValue({ items: [cell(counts)], next_cursor: null } as never)
    render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <RunsTab experimentId="experiment-1" designSpec={{}} protocol={{ ...protocol, has_unpublished_changes: unpublished }} regenerationRequired={false} unboundFactors={[]} onViewResult={vi.fn()} />
    </QueryClientProvider>)
    await screen.findByRole('button', { name: 'Run cell' })
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

  it('offers distinct recovery and repetition actions and filters the full server list', async () => {
    const user = userEvent.setup()
    await mount(false, { completed: 2, failed: 1 })
    expect(screen.getByRole('button', { name: 'Re-run all cells' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Retry failed' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Re-run cell' })).toBeInTheDocument()
    await user.type(screen.getByRole('searchbox', { name: 'Search cells and factors' }), 'model')
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by status' }), 'failed')
    await waitFor(() => expect(experimentsApi.listExecutionCells).toHaveBeenLastCalledWith('experiment-1', expect.anything(),
      expect.objectContaining({ search: 'model', status: 'failed' }), expect.anything()))
    expect(screen.getByRole('button', { name: 'Clear filters' })).toBeInTheDocument()
  })

  it('reviews a frozen failed-only batch grouped by cell before confirming', async () => {
    const user = userEvent.setup()
    const job = { id: 'batch-1', kind: 'plan', status: 'awaiting_confirmation', progress: {
      planned: 1, selected_cell_count: 1, selected_replicate_count: 1, statuses: { failed: 1 },
    }, error: null, protocol_revision_id: 'revision-1' } as const
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({ id: 'experiment-1' } as never)
    vi.spyOn(protocolsApi, 'getRevision').mockResolvedValue({ graph: { nodes: [], edges: [] } } as never)
    const create = vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(job)
    vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(job)
    vi.spyOn(experimentsApi, 'cancelResultJob').mockResolvedValue(job)
    const confirm = vi.spyOn(experimentsApi, 'confirmResultJob').mockResolvedValue({ ...job, status: 'queued' })
    await mount(false, { failed: 1, completed: 2 })
    await user.click(screen.getByRole('button', { name: 'Retry failed' }))
    const dialog = await screen.findByRole('dialog')
    await waitFor(() => expect(create).toHaveBeenCalledWith('experiment-1', expect.anything(),
      expect.objectContaining({ kind: 'plan', mode: 'retry' }), expect.anything()))
    expect(confirm).not.toHaveBeenCalled()
    expect(within(dialog).getByText('1 replicate in 1 cell will be queued.')).toBeInTheDocument()
    expect(await within(dialog).findByText('Cell ID: model=a')).toBeInTheDocument()
    await user.click(within(dialog).getByRole('button', { name: 'Retry failed (1)' }))
    await waitFor(() => expect(confirm).toHaveBeenCalledWith('experiment-1', 'batch-1'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'View affected cells' })).toBeInTheDocument()
  })

  it('marks parent selections partial and offers rerun for an iteration-limited failure', async () => {
    const user = userEvent.setup()
    vi.spyOn(experimentsApi, 'listExecutionReplicates').mockResolvedValue({ items: [{
      cell_id: 'cell-1', replicate_id: 'rep-1', replicate_number: 1, replicate_label: 'rep1',
      total: 2, matching: 2, counts: { completed: 1, failed: 1 },
    }], next_cursor: null })
    vi.spyOn(experimentsApi, 'listExecutions').mockResolvedValue({ items: [0, 1].map(index => ({
      slot_id: `slot-${index}`, cell_id: 'cell-1', cell_label: 'model=a', factor_values: { model: 'a' },
      replicate_id: 'rep-1', replicate_label: 'rep1', replicate_number: 1, row_result_id: `slot-${index}`, row_index: index,
      dataset_id: 'dataset', raw_sha256: 'hash', run_id: `run-${index}`, status: index === 0 ? 'completed' : 'failed',
      updated_at: null, error: null, result: index === 1 ? { truncated: true } : {}, usage: null,
    })), next_cursor: null })
    await mount(false, { completed: 1, failed: 1 }, true)
    await user.click(screen.getByText('Cell ID: model=a').closest('button')!)
    await user.click(await screen.findByRole('button', { name: 'Replicate 1' }))
    await user.click(await screen.findByRole('checkbox', { name: 'Select replicate 1 row 1' }))
    expect(screen.getByRole('checkbox', { name: 'Select replicate 1' })).toHaveAttribute('aria-checked', 'mixed')
    expect(screen.getByRole('checkbox', { name: 'Select cell model=a' })).toHaveAttribute('aria-checked', 'mixed')
    await user.click(screen.getByRole('checkbox', { name: 'Select replicate 1' }))
    await user.click(screen.getByRole('checkbox', { name: 'Select replicate 1 row 2' }))
    expect(screen.getByRole('checkbox', { name: 'Select replicate 1' })).toHaveAttribute('aria-checked', 'mixed')
    expect(screen.queryByRole('button', { name: 'Retry row' })).not.toBeInTheDocument()
    expect(screen.getAllByRole('button', { name: 'Re-run row' })).toHaveLength(2)
    await user.selectOptions(screen.getByRole('combobox', { name: 'Filter by status' }), 'failed')
    expect(screen.queryByRole('button', { name: 'Clear selection' })).not.toBeInTheDocument()
  })

  it('waits for a sealed retry preview before publishing a fix', async () => {
    const user = userEvent.setup()
    const job = { id: 'preparing', kind: 'plan', status: 'pending', progress: {}, error: null, protocol_revision_id: 'revision-1' } as const
    vi.spyOn(experimentsApi, 'get').mockResolvedValue({} as never)
    vi.spyOn(protocolsApi, 'getRevision').mockResolvedValue({ graph: protocol.graph } as never)
    vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(job)
    vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(job)
    vi.spyOn(experimentsApi, 'cancelResultJob').mockResolvedValue(job)
    await mount(false, { failed: 1 }, false, true)
    await user.click(screen.getByRole('button', { name: 'Retry failed' }))
    expect(await screen.findByRole('button', { name: 'Publish changes & review' })).toBeDisabled()
  })
})
