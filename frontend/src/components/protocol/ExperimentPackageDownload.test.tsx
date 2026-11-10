import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Protocol, ProtocolRevision } from '@/types/protocols'
import { ExperimentPackageDownload } from './ExperimentPackageDownload'

afterEach(() => vi.restoreAllMocks())

const protocol: Protocol = { id: 'p', name: 'Protocol', description: null, experiment_id: 'e',
  graph: { nodes: [], edges: [] }, published_revision_id: 'current', published_revision: 2,
  has_unpublished_changes: true, created_at: '', updated_at: '' }
const versions: ProtocolRevision[] = [
  { id: 'old', protocol_id: 'p', revision: 1, graph: { nodes: [], edges: [] }, published_at: '2026-01-01', experiment_snapshot: null },
  { id: 'current', protocol_id: 'p', revision: 2, graph: { nodes: [], edges: [] }, published_at: '2026-01-02' },
]

function show(selectedVersionId?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ExperimentPackageDownload experimentId="e" protocol={protocol}
    selectedVersionId={selectedVersionId} /></QueryClientProvider>)
}

it('starts from the viewed historical publication and explicitly opts into results', async () => {
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue(versions)
  vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue({ id: 'job', kind: 'export', status: 'pending',
    protocol_revision_id: 'old', progress: {}, error: null })
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue({ id: 'job', kind: 'export', status: 'completed',
    protocol_revision_id: 'old', progress: {}, error: null, download_url: '/old-package', schema_url: '/manifest' })
  show('old')
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2))
  expect(screen.getByRole('combobox', { name: 'Published version' })).toHaveValue('old')
  expect(screen.getByRole('checkbox')).not.toBeChecked()
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Prepare package' }))
  await waitFor(() => expect(experimentsApi.createResultJob).toHaveBeenCalledWith('e',
    { protocol_id: 'p', protocol_revision_id: 'old' },
    expect.objectContaining({ kind: 'export', format: 'experiment_package', include_results: true })))
  expect(await screen.findByRole('link', { name: 'Download prepared package' })).toHaveAttribute('href', '/old-package')
  expect(screen.getByRole('link', { name: 'Manifest' })).toHaveAttribute('href', '/manifest')
})

it('defaults to the published version and sends results excluded', async () => {
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue(versions)
  vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue({ id: 'job', kind: 'export', status: 'failed',
    protocol_revision_id: 'current', progress: {}, error: 'Package unavailable' })
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue({ id: 'job', kind: 'export', status: 'failed',
    protocol_revision_id: 'current', progress: {}, error: 'Package unavailable' })
  show()
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2))
  expect(screen.getByRole('combobox', { name: 'Published version' })).toHaveValue('current')
  fireEvent.click(screen.getByRole('button', { name: 'Prepare package' }))
  await waitFor(() => expect(experimentsApi.createResultJob).toHaveBeenCalledWith('e',
    { protocol_id: 'p', protocol_revision_id: 'current' },
    expect.objectContaining({ include_results: false })))
  expect(await screen.findByRole('alert')).toHaveTextContent('Package unavailable')
})

const completed = { id: 'job', kind: 'export', status: 'completed', protocol_revision_id: 'old',
  progress: {}, error: null, download_url: '/package?token=expired', schema_url: '/manifest?token=expired' } as const

async function prepareCompletedPackage() {
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue(versions)
  vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(completed)
  const status = vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(completed)
  show('old')
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2))
  fireEvent.click(screen.getByRole('button', { name: 'Prepare package' }))
  await screen.findByRole('link', { name: 'Download prepared package' })
  return status
}

it('renews stale archive and manifest URLs on activation and uses native navigation', async () => {
  const status = await prepareCompletedPackage()
  const navigated: string[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    navigated.push(this.getAttribute('href') ?? '')
  })
  status.mockResolvedValue({ ...completed, download_url: '/package?token=fresh', schema_url: '/manifest?token=fresh' })
  fireEvent.click(screen.getByRole('link', { name: 'Download prepared package' }))
  await waitFor(() => expect(navigated).toEqual(['/package?token=fresh']))
  fireEvent.click(screen.getByRole('link', { name: 'Manifest' }))
  await waitFor(() => expect(navigated).toEqual(['/package?token=fresh', '/manifest?token=fresh']))
  expect(status).toHaveBeenCalledTimes(3)
  expect(status).toHaveBeenLastCalledWith('e', 'job', expect.any(AbortSignal))
})

it('reports link renewal failure without navigating to the expired URL', async () => {
  const status = await prepareCompletedPackage()
  const navigate = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  status.mockRejectedValue(new Error('Status unavailable'))
  fireEvent.click(screen.getByRole('link', { name: 'Download prepared package' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Could not refresh the download link. Try again.')
  expect(navigate).not.toHaveBeenCalled()
})

it('does not navigate after the selected version changes while renewal is pending', async () => {
  const status = await prepareCompletedPackage()
  const navigate = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  let finish!: (value: Awaited<ReturnType<typeof experimentsApi.getResultJob>>) => void
  status.mockImplementation(() => new Promise(resolve => { finish = resolve }))
  fireEvent.click(screen.getByRole('link', { name: 'Download prepared package' }))
  await waitFor(() => expect(status).toHaveBeenCalledTimes(2))
  fireEvent.change(screen.getByRole('combobox', { name: 'Published version' }), { target: { value: 'current' } })
  await act(async () => { finish({ ...completed, download_url: '/package?token=fresh' }) })
  expect(navigate).not.toHaveBeenCalled()
  expect(screen.queryByRole('link', { name: 'Download prepared package' })).not.toBeInTheDocument()
})
