import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Experiment } from '@/types/experiments'
import type { Protocol } from '@/types/protocols'
import { ProtocolCanvasMenu } from './ProtocolCanvasMenu'
import { protocolForExperimentQueryKey, protocolGraphQueryKey } from '@/lib/protocolGraph'

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals() })

const protocol: Protocol = { id: 'p', name: 'Protocol', description: null, experiment_id: 'e',
  graph: { nodes: [], edges: [] }, published_revision_id: 'published', published_revision: 1,
  has_unpublished_changes: false, created_at: '', updated_at: '' }

function show(draft = protocol, draftBusy = false) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(protocolForExperimentQueryKey('e'), draft)
  vi.spyOn(protocolsApi, 'get').mockResolvedValue(draft)
  vi.spyOn(experimentsApi, 'get').mockResolvedValue({ id: 'e', name: 'Experiment', description: null } as Experiment)
  vi.stubGlobal('URL', class extends URL {
    static createObjectURL() { return 'blob:legacy-json' }
    static revokeObjectURL() {}
  })
  render(<QueryClientProvider client={client}><MemoryRouter>
    <ProtocolCanvasMenu protocolId="p" experimentId="e" nodes={[]} edges={[]} draftBusy={draftBusy} />
  </MemoryRouter></QueryClientProvider>)
  return client
}

it('downloads a package from the actual canvas menu instead of a JSON definition', async () => {
  const user = userEvent.setup()
  const job = { id: 'export', kind: 'export', status: 'completed', protocol_revision_id: 'published',
    progress: {}, error: null, download_url: '/api/experiment-package.tar.gz' } as const
  const create = vi.spyOn(experimentsApi, 'createResultJob').mockResolvedValue(job)
  vi.spyOn(experimentsApi, 'getResultJob').mockResolvedValue(job)
  const downloads: string[] = []
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    downloads.push(this.getAttribute('href') ?? '')
  })
  show()
  await user.click(screen.getByRole('button', { name: 'Experiment menu' }))
  await user.click(await screen.findByRole('menuitem', { name: 'Download' }))
  await waitFor(() => expect(downloads).toEqual(['/api/experiment-package.tar.gz']))
  expect(create).toHaveBeenCalledWith('e', { protocol_id: 'p', protocol_revision_id: 'published' },
    expect.objectContaining({ kind: 'export', format: 'experiment_package' }))
})

it.each([
  { draft: { ...protocol, has_unpublished_changes: true }, draftBusy: false },
  { draft: { ...protocol, published_revision_id: null, published_revision: null }, draftBusy: false },
  { draft: protocol, draftBusy: true },
])('requires publication from the canvas menu without publishing or downloading JSON: %j', async ({ draft, draftBusy }) => {
  const user = userEvent.setup()
  const create = vi.spyOn(experimentsApi, 'createResultJob')
  const publish = vi.spyOn(protocolsApi, 'publish')
  const download = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  show(draft, draftBusy)
  await user.click(screen.getByRole('button', { name: 'Experiment menu' }))
  await user.click(await screen.findByRole('menuitem', { name: 'Download' }))
  expect(await screen.findByText('Publish before exporting')).toBeInTheDocument()
  expect(create).not.toHaveBeenCalled()
  expect(publish).not.toHaveBeenCalled()
  expect(download).not.toHaveBeenCalled()
})

it('checks live canvas edits before autosave when downloading from the menu', async () => {
  const user = userEvent.setup()
  const create = vi.spyOn(experimentsApi, 'createResultJob')
  const client = show()
  client.setQueryData(protocolGraphQueryKey('p'), {
    nodes: [{ id: 'unsaved', type: 'agent', position: { x: 0, y: 0 }, data: {} }], edges: [],
  })
  await user.click(screen.getByRole('button', { name: 'Experiment menu' }))
  await user.click(await screen.findByRole('menuitem', { name: 'Download' }))
  expect(await screen.findByText('Publish before exporting')).toBeInTheDocument()
  expect(create).not.toHaveBeenCalled()
})
