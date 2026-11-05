import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Protocol } from '@/types/protocols'
import type { ExecutionSlot, ResultJob } from '@/types/executionResults'
import { RunAllCellsButton, RunsTab } from './RunsTab'
import { ResultsPanel } from './ResultsPanel'
import { protocolForExperimentQueryKey } from '@/lib/protocolGraph'

vi.mock('./RunConfirmDialog', () => ({ RunConfirmDialog: ({ additionalContent, onConfirm, onPublishAndRun, onCancel, confirmDisabled, publishDisabled, publishLabel, publishError }: {
  additionalContent: React.ReactNode; onConfirm: () => void; onPublishAndRun: () => void; onCancel: () => void
  confirmDisabled: boolean; publishDisabled: boolean; publishLabel?: string; publishError?: string
}) => <div>{additionalContent}{publishError && <p role="alert">{publishError}</p>}<button onClick={onCancel}>Cancel batch</button><button disabled={confirmDisabled} onClick={onConfirm}>Confirm batch</button><button disabled={publishDisabled} onClick={onPublishAndRun}>{publishLabel ?? 'Publish & Run all cells'}</button></div> }))
vi.mock('./ExperimentDefinitionDownload', () => ({ ExperimentDefinitionDownload: () => null }))
const row: ExecutionSlot = { slot_id: 'stable-slot', cell_id: 'cell', cell_label: 'model=a', factor_values: {},
  replicate_id: 'parent', replicate_label: 'ambiguous-label', replicate_number: 1, row_result_id: null, row_index: null,
  dataset_id: null, raw_sha256: null, run_id: 'attempt', status: 'completed', updated_at: null, error: null, result: {}, usage: null }
vi.mock('./ExecutionBrowser', () => ({ ExecutionBrowser: ({ batchControls }: { batchControls: React.ReactNode }) => <>{batchControls}</>, ExecutionRows: ({ actions }: { actions: (row: ExecutionSlot) => React.ReactNode }) => <div>{actions(row)}</div> }))
const protocol = { id: 'protocol', published_revision_id: 'publication', published_revision: 1, graph: { nodes: [], edges: [] } } as unknown as Protocol
const pending: ResultJob = { id: 'job', kind: 'plan', status: 'pending', progress: {}, error: null, protocol_revision_id: 'publication' }

function mount(children: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>)
}
function ObservedProtocol({ draft }: { draft: Protocol }) {
  const { data } = useQuery({ queryKey: protocolForExperimentQueryKey('experiment'),
    queryFn: async () => draft, initialData: draft, staleTime: Infinity })
  return <RunsTab protocol={data} experimentId="experiment" designSpec={null} regenerationRequired={false} unboundFactors={[]} onViewResult={() => {}} />
}
beforeEach(() => {
  vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(pending)
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue({ ...pending, status: 'awaiting_confirmation', progress: { planned: 15786 } })
  vi.spyOn(experimentsApi, 'confirmResultJob').mockResolvedValue({ ...pending, status: 'queued' })
  vi.spyOn(experimentsApi, 'cancelResultJob').mockResolvedValue({ ...pending, status: 'cancelled' })
  vi.spyOn(experimentsApi, 'getExecutionSummary').mockResolvedValue({ ready: false, metric_types: {}, metric_directions: {} } as never)
  vi.spyOn(experimentsApi, 'get').mockResolvedValue({ locked_at: 'already locked' } as never)
  vi.spyOn(protocolsApi, 'runCells')
  vi.spyOn(experimentsApi, 'downloadRunResultsCsv')
})
afterEach(() => vi.restoreAllMocks())

describe('asynchronous result commands', () => {
  it('prepares without executing, then confirms the sealed job independently of summary preparation', async () => {
    mount(<RunAllCellsButton protocol={protocol} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 run attempts will be queued.')
    expect(protocolsApi.runCells).not.toHaveBeenCalled()
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm batch' }))
    await waitFor(() => expect(experimentsApi.confirmResultJob).toHaveBeenCalledWith('experiment', 'job'))
    expect(experimentsApi.createResultJob).toHaveBeenCalledWith('experiment',
      { protocol_id: 'protocol', protocol_revision_id: 'publication' },
      expect.objectContaining({ kind: 'plan' }), expect.any(AbortSignal))
  })

  it('selects completed work by slot ID and abandons the previous preview safely', async () => {
    mount(<RunAllCellsButton protocol={protocol} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 run attempts will be queued.')
    fireEvent.click(screen.getByRole('checkbox', { name: 'Run replicate 1 again' }))
    await waitFor(() => expect(experimentsApi.createResultJob).toHaveBeenLastCalledWith('experiment', expect.anything(),
      expect.objectContaining({ rerun_slot_ids: ['stable-slot'] }), expect.any(AbortSignal)))
    expect(experimentsApi.cancelResultJob).toHaveBeenCalledWith('experiment', 'job', true)
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
  })

  it('offers iteration-limited work to run again instead of re-running it implicitly', async () => {
    const status = row.status
    row.status = 'limit_reached'
    try {
      mount(<RunAllCellsButton protocol={protocol} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
      fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
      await screen.findByText('15786 run attempts will be queued.')
      expect(screen.getByText(/including ones that hit their iteration limit, are skipped unless selected/)).toBeInTheDocument()
      fireEvent.click(screen.getByRole('checkbox', { name: 'Run replicate 1 again' }))
      await waitFor(() => expect(experimentsApi.createResultJob).toHaveBeenLastCalledWith('experiment', expect.anything(),
        expect.objectContaining({ rerun_slot_ids: ['stable-slot'] }), expect.any(AbortSignal)))
    } finally {
      row.status = status
    }
  })

  it('allows first publication while the batch preview is unavailable', () => {
    mount(<RunAllCellsButton protocol={{ ...protocol, published_revision_id: null, published_revision: null }} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    expect(screen.getByRole('button', { name: 'Publish & Run all cells' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Confirm batch' })).toBeDisabled()
    expect(experimentsApi.createResultJob).not.toHaveBeenCalled()
  })

  it.each([false, true])('publishes and queues the new version without a second confirmation (previously published: %s)', async (previouslyPublished) => {
    const draft = { ...protocol, has_unpublished_changes: true,
      published_revision_id: previouslyPublished ? 'old-publication' : null,
      published_revision: previouslyPublished ? 1 : null }
    const published = { ...protocol, published_revision_id: 'new-publication', published_revision: 2, has_unpublished_changes: false }
    vi.spyOn(protocolsApi, 'publish').mockResolvedValue(published)
    vi.mocked(experimentsApi.createResultJob).mockImplementation(async (_experiment, scope) => ({
      ...pending, id: scope.protocol_revision_id === 'new-publication' ? 'new-job' : 'old-job',
      protocol_revision_id: scope.protocol_revision_id!,
    }))
    let completePreview!: (job: ResultJob) => void
    vi.mocked(experimentsApi.getResultJob).mockImplementation(async (_experiment, id) => id === 'new-job'
      ? new Promise<ResultJob>(resolve => { completePreview = resolve })
      : { ...pending, id, status: 'awaiting_confirmation', progress: { planned: 60 }, protocol_revision_id: 'old-publication' })
    mount(<ObservedProtocol draft={draft} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    if (previouslyPublished) await screen.findByText('60 run attempts will be queued.')
    fireEvent.click(screen.getByRole('button', { name: 'Publish & Run all cells' }))
    await waitFor(() => expect(protocolsApi.publish).toHaveBeenCalledWith('protocol'))
    await waitFor(() => expect(completePreview).toBeTypeOf('function'))
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
    completePreview({ ...pending, id: 'new-job', protocol_revision_id: 'new-publication',
      status: 'awaiting_confirmation', progress: { planned: 60 } })
    await waitFor(() => expect(experimentsApi.confirmResultJob).toHaveBeenCalledWith('experiment', 'new-job'))
    expect(experimentsApi.confirmResultJob).toHaveBeenCalledOnce()
    expect(protocolsApi.runCells).not.toHaveBeenCalled()
  })

  it('does not queue an old preview when publishing fails', async () => {
    vi.spyOn(protocolsApi, 'publish').mockRejectedValue(new Error('Publication failed'))
    mount(<ObservedProtocol draft={{ ...protocol, has_unpublished_changes: true }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 run attempts will be queued.')
    fireEvent.click(screen.getByRole('button', { name: 'Publish & Run all cells' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Publication failed')
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
  })

  it('cancels the requested run if the dialog closes while publishing', async () => {
    let finishPublish!: (value: Protocol) => void
    vi.spyOn(protocolsApi, 'publish').mockImplementation(() => new Promise(resolve => { finishPublish = resolve }))
    mount(<ObservedProtocol draft={{ ...protocol, published_revision_id: null, published_revision: null }} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    fireEvent.click(screen.getByRole('button', { name: 'Publish & Run all cells' }))
    await waitFor(() => expect(finishPublish).toBeTypeOf('function'))
    fireEvent.click(screen.getByRole('button', { name: 'Cancel batch' }))
    finishPublish({ ...protocol, published_revision_id: 'new-publication', published_revision: 2 })
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Confirm batch' })).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 run attempts will be queued.')
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
  })

  it('downloads a completed background export natively without constructing a browser CSV blob', async () => {
    vi.mocked(experimentsApi.getResultJob).mockResolvedValue({ ...pending, kind: 'export', status: 'completed',
      download_url: '/api/owned-export?token=signed', schema_url: '/api/owned-schema?token=signed' })
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
    mount(<ResultsPanel experimentId="experiment" experimentName="Large experiment" scope={{ protocol_revision_id: 'publication' }} summary={<p>Summary</p>} cells={<p>Cells</p>} />)
    fireEvent.click(screen.getByRole('button', { name: 'Download CSV' }))
    await waitFor(() => expect(click).toHaveBeenCalledOnce())
    expect(experimentsApi.downloadRunResultsCsv).not.toHaveBeenCalled()
    expect(screen.getByRole('link', { name: 'Download prepared CSV' })).toHaveAttribute('href', '/api/owned-export?token=signed')
  })
})
