import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi, packageImportsApi } from '@/api/client'
import type { Protocol, ProtocolRevision } from '@/types/protocols'
import { ExperimentPackageDownload } from './ExperimentPackageDownload'
import { protocolForExperimentQueryKey, protocolGraphQueryKey } from '@/lib/protocolGraph'

afterEach(() => vi.restoreAllMocks())

const protocol: Protocol = { id: 'p', name: 'Protocol', description: null, experiment_id: 'e',
  graph: { nodes: [], edges: [] }, published_revision_id: 'current', published_revision: 2,
  has_unpublished_changes: true, created_at: '', updated_at: '' }
const versions: ProtocolRevision[] = [
  { id: 'old', protocol_id: 'p', revision: 1, graph: { nodes: [], edges: [] }, published_at: '2026-01-01', experiment_snapshot: null },
  { id: 'current', protocol_id: 'p', revision: 2, graph: { nodes: [], edges: [] }, published_at: '2026-01-02' },
]

function show(selectedVersionId?: string, draft = protocol) {
  vi.spyOn(packageImportsApi, 'list').mockResolvedValue([])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ExperimentPackageDownload experimentId="e" protocol={draft}
    selectedVersionId={selectedVersionId} /></QueryClientProvider>)
  return client
}

it('warns about unpublished changes and publishes the live canvas before packaging the returned version', async () => {
  const published = { ...protocol, published_revision_id: 'new', published_revision: 3, has_unpublished_changes: false }
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValueOnce(versions)
    .mockResolvedValue([...versions, { ...versions[1], id: 'new', revision: 3 }])
  let finishSave!: (value: Protocol) => void
  const save = vi.spyOn(protocolsApi, 'update').mockImplementation(() => new Promise(resolve => { finishSave = resolve }))
  const publish = vi.spyOn(protocolsApi, 'publish').mockResolvedValue(published)
  const prepared = { id: 'new-job', kind: 'export', status: 'completed', protocol_revision_id: 'new', progress: {}, error: null } as const
  const prepare = vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(prepared)
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(prepared)
  const client = show('old')
  const graph = { nodes: [{ id: 'unsaved', type: 'agent', position: { x: 0, y: 0 }, data: { label: 'Unsaved agent' } }], edges: [] }
  client.setQueryData(protocolGraphQueryKey('p'), graph)
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  expect(await screen.findByText('Experiment has unpublished changes')).toBeInTheDocument()
  await waitFor(() => expect(screen.getAllByRole('option')).toHaveLength(2))
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(screen.getByRole('button', { name: 'Publish & prepare package' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('p', { graph }))
  expect(publish).not.toHaveBeenCalled()
  expect(prepare).not.toHaveBeenCalled()
  expect(screen.getByRole('combobox')).toBeDisabled()
  await act(async () => { finishSave(protocol) })
  await waitFor(() => expect(prepare).toHaveBeenCalledWith('e',
    { protocol_id: 'p', protocol_revision_id: 'new' }, expect.objectContaining({ include_results: true })))
  expect(publish).toHaveBeenCalledWith('p')
  expect(client.getQueryData(protocolForExperimentQueryKey('e'))).toEqual(published)
  expect(screen.getByRole('combobox')).toHaveValue('new')
})

it('allows publication and package preparation before the first version exists', async () => {
  const published = { ...protocol, has_unpublished_changes: false }
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValueOnce([]).mockResolvedValue([versions[1]])
  vi.spyOn(protocolsApi, 'publish').mockResolvedValue(published)
  const prepare = vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue({ id: 'job', kind: 'export', status: 'failed',
    protocol_revision_id: 'current', progress: {}, error: 'fixture' })
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue({ id: 'job', kind: 'export', status: 'failed',
    protocol_revision_id: 'current', progress: {}, error: 'fixture' })
  show(undefined, { ...protocol, published_revision_id: null, published_revision: null })
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  expect(await screen.findByText('Experiment has not been published')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'Prepare package' })).toBeDisabled()
  fireEvent.click(screen.getByRole('button', { name: 'Publish & prepare package' }))
  await waitFor(() => expect(prepare).toHaveBeenCalledWith('e',
    { protocol_id: 'p', protocol_revision_id: 'current' }, expect.anything()))
})

it('reports publication failure without preparing an older version', async () => {
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue(versions)
  vi.spyOn(protocolsApi, 'publish').mockRejectedValue(new Error('Generate cells before publishing.'))
  const prepare = vi.spyOn(experimentsApi, 'createResultJob')
  show('old')
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  fireEvent.click(await screen.findByRole('button', { name: 'Publish & prepare package' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Generate cells before publishing.')
  expect(prepare).not.toHaveBeenCalled()
  expect(screen.getByRole('combobox')).toHaveValue('old')
})

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

it('does not offer publication for an unchanged published experiment', async () => {
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue(versions)
  show(undefined, { ...protocol, has_unpublished_changes: false })
  fireEvent.click(screen.getByRole('button', { name: 'Download experiment package' }))
  await screen.findByRole('combobox')
  expect(screen.queryByRole('button', { name: 'Publish & prepare package' })).not.toBeInTheDocument()
  expect(screen.queryByText('Experiment has unpublished changes')).not.toBeInTheDocument()
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
