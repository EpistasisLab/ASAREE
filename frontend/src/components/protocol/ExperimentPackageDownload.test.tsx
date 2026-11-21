import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Protocol } from '@/types/protocols'
import { ExperimentPackageDownload } from './ExperimentPackageDownload'
import { protocolGraphQueryKey } from '@/lib/protocolGraph'

afterEach(() => vi.restoreAllMocks())
const protocol: Protocol = { id: 'p', name: 'Protocol', description: null, experiment_id: 'e',
  graph: { nodes: [], edges: [] }, published_revision_id: 'current', published_revision: 2,
  has_unpublished_changes: false, created_at: '', updated_at: '' }
function show(draft = protocol, selectedVersionId?: string, draftBusy = false) {
  vi.spyOn(protocolsApi, 'get').mockResolvedValue(draft)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><ExperimentPackageDownload experimentId="e" protocol={draft}
    selectedVersionId={selectedVersionId} draftBusy={draftBusy} /></QueryClientProvider>)
  return client
}

it.each([
  { ...protocol, has_unpublished_changes: true },
  { ...protocol, published_revision_id: null, published_revision: null },
])('requires publication and offers no publish shortcut', async draft => {
  const create = vi.spyOn(experimentsApi, 'createResultJob')
  const publish = vi.spyOn(protocolsApi, 'publish')
  show(draft)
  fireEvent.click(screen.getByRole('button', { name: 'Download' }))
  expect(await screen.findByText('Publish before exporting')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /Publish/ })).not.toBeInTheDocument()
  expect(create).not.toHaveBeenCalled()
  expect(publish).not.toHaveBeenCalled()
})

it('blocks a canvas edit that has not reached autosave', async () => {
  const create = vi.spyOn(experimentsApi, 'createResultJob')
  const client = show()
  client.setQueryData(protocolGraphQueryKey('p'), { nodes: [{ id: 'new', type: 'agent', position: { x: 0, y: 0 }, data: {} }], edges: [] })
  fireEvent.click(screen.getByRole('button', { name: 'Download' }))
  expect(await screen.findByText('Publish before exporting')).toBeInTheDocument()
  expect(create).not.toHaveBeenCalled()
})

it('exports the published version without a results option and starts the download automatically', async () => {
  const job = { id: 'job', kind: 'export', status: 'completed', protocol_revision_id: 'current',
    progress: {}, error: null, download_url: '/package' } as const
  const create = vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(job)
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(job)
  const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  show()
  fireEvent.click(screen.getByRole('button', { name: 'Download' }))
  await waitFor(() => expect(download).toHaveBeenCalledTimes(1))
  expect(create).toHaveBeenCalledWith('e', { protocol_id: 'p', protocol_revision_id: 'current' },
    expect.objectContaining({ kind: 'export', format: 'experiment_package' }))
  expect(create.mock.calls[0][2]).not.toHaveProperty('include_results')
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
})

it('exports an explicitly viewed historical publication while the draft has changes', async () => {
  const job = { id: 'job', kind: 'export', status: 'failed', protocol_revision_id: 'old', progress: {}, error: 'Missing Skill file' } as const
  const create = vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(job)
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(job)
  show({ ...protocol, has_unpublished_changes: true }, 'old')
  fireEvent.click(screen.getByRole('button', { name: 'Download' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Missing Skill file')
  expect(create).toHaveBeenCalledWith('e', { protocol_id: 'p', protocol_revision_id: 'old' }, expect.anything())
})
