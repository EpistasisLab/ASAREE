import type { ReactNode } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import { executionKeys } from '@/lib/executionQueries'
import type { ExecutionCell, ExecutionSlot, ResultJob } from '@/types/executionResults'
import type { Protocol, ProtocolRun } from '@/types/protocols'
import { RunsTab } from './RunsTab'

const state = vi.hoisted(() => ({ active: true, status: 'running', runId: 'attempt' }))
vi.mock('@/lib/executionQueries', async importOriginal => ({
  ...await importOriginal<typeof import('@/lib/executionQueries')>(),
  useExecutionSummary: () => ({ data: { has_active: state.active } }),
}))
vi.mock('./ExecutionBrowser', () => ({
  ExecutionRows: () => null,
  ExecutionBrowser: ({ controls, cellActions, actions }: {
    controls: ReactNode; cellActions: (cell: ExecutionCell) => ReactNode; actions: (row: ExecutionSlot) => ReactNode
  }) => <>{controls}{cellActions({ cell_id: 'cell', cell_label: 'Cell', factor_values: {}, counts: { running: Number(state.active) } })}
    {actions({ run_id: state.runId, status: state.status, result: {}, replicate_number: 1 } as ExecutionSlot)}</>,
}))
const protocol = { id: 'protocol', published_revision_id: 'publication', graph: { nodes: [], edges: [] } } as unknown as Protocol
const cancelJob: ResultJob = { id: 'stop-job', kind: 'cancel', status: 'completed', progress: { cancelled: 2 }, error: null, protocol_revision_id: 'publication' }
const panel = () => <RunsTab experimentId="experiment" protocol={protocol} designSpec={null} regenerationRequired={false} unboundFactors={[]} onViewResult={() => {}} />
function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })
  const view = render(panel(), { wrapper: ({ children }) => <QueryClientProvider client={client}>{children}</QueryClientProvider> })
  return { ...view, client }
}
beforeEach(() => {
  state.active = true
  state.status = 'running'
  state.runId = 'attempt'
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(cancelJob)
})
afterEach(() => vi.restoreAllMocks())

it.each(['Stop all', 'Stop cell'])('%s shows immediate feedback and waits for active attempts after requests complete', async label => {
  let accept!: (job: ResultJob) => void
  vi.spyOn(experimentsApi, 'createResultJob').mockImplementation(() => new Promise(resolve => { accept = resolve }))
  const { rerender } = mount()
  fireEvent.click(screen.getByRole('button', { name: label }))
  const stoppingLabel = label === 'Stop all' ? 'Stopping all…' : 'Stopping cell…'
  expect(screen.getByRole('button', { name: stoppingLabel })).toBeDisabled()
  expect(screen.getByText(/queued attempts are being cancelled/)).toBeInTheDocument()
  await waitFor(() => expect(accept).toBeTypeOf('function'))
  accept(cancelJob)
  await screen.findByText('Cancellation requests · completed · 2 requested')
  expect(screen.getByRole('button', { name: stoppingLabel })).toBeDisabled()
  state.active = false
  state.status = 'cancelled'
  rerender(panel())
  await waitFor(() => expect(screen.queryByText(/queued attempts are being cancelled/)).not.toBeInTheDocument())
  expect(screen.getByRole('button', { name: label })).toBeDisabled()
})

it('keeps an individual attempt stopping after acceptance, refreshes executions, and resets for a new attempt', async () => {
  vi.spyOn(protocolsApi, 'cancelRun').mockResolvedValue({} as ProtocolRun)
  const { client, rerender } = mount()
  const invalidate = vi.spyOn(client, 'invalidateQueries')
  fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
  await waitFor(() => expect(protocolsApi.cancelRun).toHaveBeenCalledWith('protocol', 'attempt'))
  expect(screen.getByRole('button', { name: 'Stopping…' })).toBeDisabled()
  expect(invalidate).toHaveBeenCalledWith({ queryKey: executionKeys.root('experiment') })
  state.runId = 'new-attempt'
  rerender(panel())
  expect(screen.getByRole('button', { name: 'Stop' })).toBeEnabled()
})

it('reports a rejected stop request and allows retrying', async () => {
  vi.spyOn(experimentsApi, 'createResultJob').mockRejectedValue(new Error('Stop request failed'))
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Stop all' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Stop request failed')
  expect(screen.getByRole('button', { name: 'Stop all' })).toBeEnabled()
  expect(screen.queryByText(/queued attempts are being cancelled/)).not.toBeInTheDocument()
})

it('allows retrying when the background cancellation job fails', async () => {
  vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(cancelJob)
  vi.mocked(experimentsApi.getResultJob).mockResolvedValue({ ...cancelJob, status: 'failed', error: 'Cancellation interrupted' })
  mount()
  fireEvent.click(screen.getByRole('button', { name: 'Stop cell' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Cancellation interrupted')
  await waitFor(() => expect(screen.getByRole('button', { name: 'Stop cell' })).toBeEnabled())
  expect(screen.queryByText(/queued attempts are being cancelled/)).not.toBeInTheDocument()
})
