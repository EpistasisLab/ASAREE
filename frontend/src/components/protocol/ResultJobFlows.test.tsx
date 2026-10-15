import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Protocol } from '@/types/protocols'
import type { ResultJob } from '@/types/executionResults'
import { RunAllCellsButton } from './RunsTab'
import { ResultsPanel } from './ResultsPanel'

vi.mock('./RunConfirmDialog', () => ({ RunConfirmDialog: ({ additionalContent, onConfirm, onPublishAndRun, confirmDisabled, publishDisabled }: {
  additionalContent: React.ReactNode; onConfirm: () => void; onPublishAndRun: () => void; confirmDisabled: boolean; publishDisabled: boolean
}) => <div>{additionalContent}<button disabled={confirmDisabled} onClick={onConfirm}>Confirm batch</button><button disabled={publishDisabled} onClick={onPublishAndRun}>Publish preview</button></div> }))
vi.mock('./ExperimentDefinitionDownload', () => ({ ExperimentDefinitionDownload: () => null }))
vi.mock('./ExecutionBrowser', () => ({ ExecutionBrowser: () => null }))
const protocol = { id: 'protocol', published_revision_id: 'publication', published_revision: 1, graph: { nodes: [], edges: [] } } as unknown as Protocol
const pending: ResultJob = { id: 'job', kind: 'plan', status: 'pending', progress: {}, error: null, protocol_revision_id: 'publication' }

function mount(children: React.ReactNode) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>{children}</QueryClientProvider>)
}
beforeEach(() => {
  vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(pending)
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue({ ...pending, status: 'awaiting_confirmation', progress: { planned: 15786, selected_cell_count: 1, selected_replicate_count: 15786 } })
  vi.spyOn(experimentsApi, 'confirmResultJob').mockResolvedValue({ ...pending, status: 'queued' })
  vi.spyOn(experimentsApi, 'cancelResultJob').mockResolvedValue({ ...pending, status: 'cancelled' })
  vi.spyOn(experimentsApi, 'getExecutionSummary').mockResolvedValue({ ready: false, metric_types: {}, metric_directions: {} } as never)
  vi.spyOn(experimentsApi, 'get').mockResolvedValue({ locked_at: 'already locked' } as never)
  vi.spyOn(experimentsApi, 'getExecutionFactorOptions').mockResolvedValue({ factors: {}, truncated: false })
  vi.spyOn(protocolsApi, 'getRevision').mockResolvedValue({ graph: protocol.graph } as never)
  vi.spyOn(protocolsApi, 'runCells')
  vi.spyOn(experimentsApi, 'downloadRunResultsCsv')
})
afterEach(() => vi.restoreAllMocks())

describe('asynchronous result commands', () => {
  it('prepares without executing, then confirms the sealed job independently of summary preparation', async () => {
    mount(<RunAllCellsButton protocol={protocol} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 replicates in 1 cell will be queued.')
    expect(protocolsApi.runCells).not.toHaveBeenCalled()
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm batch' }))
    await waitFor(() => expect(experimentsApi.confirmResultJob).toHaveBeenCalledWith('experiment', 'job'))
    expect(experimentsApi.createResultJob).toHaveBeenCalledWith('experiment',
      { protocol_id: 'protocol', protocol_revision_id: 'publication' },
      expect.objectContaining({ kind: 'plan' }), expect.any(AbortSignal))
  })

  it('rebuilds a filtered preview and abandons the previous preview safely', async () => {
    mount(<RunAllCellsButton protocol={protocol} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 replicates in 1 cell will be queued.')
    fireEvent.change(screen.getByRole('searchbox', { name: 'Search cells and factors' }), { target: { value: 'model A' } })
    await waitFor(() => expect(experimentsApi.createResultJob).toHaveBeenLastCalledWith('experiment', expect.anything(),
      expect.objectContaining({ mode: 'pending', search: 'model A' }), expect.any(AbortSignal)))
    expect(experimentsApi.cancelResultJob).toHaveBeenCalledWith('experiment', 'job', true)
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
  })

  it('keeps the canvas run-all action pending-only and explains where retries live', async () => {
    mount(<RunAllCellsButton protocol={protocol} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    await screen.findByText('15786 replicates in 1 cell will be queued.')
    expect(screen.getByText(/Start only work that has never been attempted/)).toBeInTheDocument()
    expect(experimentsApi.createResultJob).toHaveBeenCalledWith('experiment', expect.anything(),
      expect.objectContaining({ mode: 'pending' }), expect.any(AbortSignal))
    expect(experimentsApi.confirmResultJob).not.toHaveBeenCalled()
  })

  it('allows first publication while the batch preview is unavailable', () => {
    mount(<RunAllCellsButton protocol={{ ...protocol, published_revision_id: null, published_revision: null }} experimentId="experiment" regenerationRequired={false} unboundFactors={[]} bindingDiscrepancies={[]} />)
    fireEvent.click(screen.getByRole('button', { name: 'Run all cells' }))
    expect(screen.getByRole('button', { name: 'Publish preview' })).toBeEnabled()
    expect(screen.getByRole('button', { name: 'Confirm batch' })).toBeDisabled()
    expect(experimentsApi.createResultJob).not.toHaveBeenCalled()
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
