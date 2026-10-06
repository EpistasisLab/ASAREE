import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, protocolsApi } from '@/api/client'
import type { Experiment } from '@/types/experiments'
import type { Protocol, ProtocolRevision } from '@/types/protocols'
import { ProtocolCanvasPage } from './ProtocolCanvasPage'

vi.mock('@/components/AppHeader', () => ({ AppHeader: () => null }))
vi.mock('@/components/protocol/ProtocolCanvas', () => ({ ProtocolCanvas: () => {
  const [draft, setDraft] = useState('Unsaved draft')
  return <input aria-label="Draft canvas" value={draft} onChange={event => setDraft(event.target.value)} />
} }))
vi.mock('@/components/protocol/ExperimentVersionView', () => ({ ExperimentVersionCanvas: ({ version }: { version: ProtocolRevision }) => <p>Saved canvas {version.revision}</p> }))
vi.mock('@/components/protocol/ResultsTab', () => ({ ResultsInspectorPanel: () => null }))
vi.mock('@/components/protocol/ExperimentSidePanel', () => ({ ExperimentSidePanel: (props: { viewingHistory: boolean; version?: ProtocolRevision; draftExperiment: Experiment; resultsExperiment: Experiment }) => <div aria-label="Experiment panels"><p>{props.viewingHistory ? `Historical design: ${props.version?.experiment_snapshot?.hypothesis}` : `Draft design: ${props.draftExperiment?.hypothesis}`}</p><p>Results design: {props.resultsExperiment?.hypothesis}</p></div> }))
afterEach(() => vi.restoreAllMocks())

it('switches canvas and design together and preserves local draft edits on return', async () => {
  const experiment = { id: 'e', name: 'Experiment', hypothesis: 'Current', design_spec: {}, design_type: 'factorial' } as Experiment
  const protocol = { id: 'p', name: 'Protocol', description: null, created_at: '2026-01-01', updated_at: '2026-01-01', experiment_id: 'e', graph: { nodes: [], edges: [] }, published_revision_id: 'v2', published_revision: 2, has_unpublished_changes: true } as Protocol
  const versions = [{ id: 'v1', revision: 1, graph: { nodes: [], edges: [] }, published_at: '2026-01-01', experiment_snapshot: { hypothesis: 'Original', design_spec: {} } }, { id: 'v2', revision: 2, graph: { nodes: [], edges: [] }, published_at: '2026-01-02', experiment_snapshot: { hypothesis: 'Published', design_spec: {} } }] as unknown as ProtocolRevision[]
  vi.spyOn(experimentsApi, 'get').mockResolvedValue(experiment)
  vi.spyOn(experimentsApi, 'listReplicates').mockResolvedValue([])
  vi.spyOn(experimentsApi, 'getDesignImpact').mockResolvedValue({} as Awaited<ReturnType<typeof experimentsApi.getDesignImpact>>)
  vi.spyOn(experimentsApi, 'getRunResults').mockRejectedValue(new Error('No results'))
  vi.spyOn(experimentsApi, 'listTrials').mockResolvedValue([])
  vi.spyOn(protocolsApi, 'list').mockResolvedValue([protocol])
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue([...versions, { id: 'legacy', protocol_id: 'p', revision: 0, graph: { nodes: [], edges: [] }, published_at: '2025-12-01', experiment_snapshot: null }])
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/experiments/e/protocol']}><Routes><Route path="/experiments/:experimentId/protocol" element={<ProtocolCanvasPage />} /></Routes></MemoryRouter></QueryClientProvider>)
  const draft = await screen.findByRole('textbox', { name: 'Draft canvas' })
  await screen.findByText('Results design: Published')
  const historyButton = screen.getByRole('button', { name: 'Version history' })
  expect(historyButton.closest('[aria-label="Experiment version controls"]')).toBeInTheDocument()
  expect(screen.getByLabelText('Experiment panels')).not.toContainElement(historyButton)
  expect(screen.queryByRole('combobox', { name: 'Experiment version' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('Experiment version')).not.toBeInTheDocument()
  fireEvent.change(draft, { target: { value: 'Local edit' } })
  fireEvent.click(screen.getByRole('button', { name: 'Version history' }))
  expect(await screen.findByRole('button', { name: 'View Version 1' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'View Version 0' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'View Version 1' }))
  expect(screen.getByText('Saved canvas 1')).toBeInTheDocument()
  expect(screen.getByText('Historical design: Original')).toBeInTheDocument()
  expect(screen.getByText('Results design: Original')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Publish experiment' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Return to experiment' }))
  expect(screen.getByRole('textbox', { name: 'Draft canvas' })).toHaveValue('Local edit')
  expect(screen.getByText('Draft design: Current')).toBeInTheDocument()
  expect(screen.getByText('Results design: Published')).toBeInTheDocument()
  await act(async () => {
    client.setQueryData(['protocols', 'for-experiment', 'e'], { ...protocol, has_unpublished_changes: false })
  })
  await waitFor(() => expect(screen.getByText('Published v2')).toBeInTheDocument())

  // The API order need not be newest first, and old selections remain reachable.
  await act(async () => {
    client.setQueryData(['protocols', 'p', 'revisions'], Array.from({ length: 9 }, (_, index) => ({
      ...versions[0], id: `v${index + 1}`, revision: index + 1, published_at: `2026-01-0${index + 1}`,
    })))
  })
  fireEvent.click(screen.getByRole('button', { name: 'Version history' }))
  const search = await screen.findByRole('textbox', { name: 'Search versions' })
  expect(screen.getAllByRole('button', { name: /^View Version/ }).map(button => button.textContent)).toEqual(['View Version 2', 'View Version 9', 'View Version 8', 'View Version 7', 'View Version 6', 'View Version 5', 'View Version 4', 'View Version 3', 'View Version 1'])
  fireEvent.change(search, { target: { value: 'no match' } })
  expect(screen.getByText('No versions match your search.')).toBeInTheDocument()
  fireEvent.change(search, { target: { value: '2026-01-01' } })
  expect(screen.getByText('1 of 9 versions · newest first')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'View Version 1' }))
  expect(screen.getByText('Saved canvas 1')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Version history' }))
  expect(await screen.findByRole('textbox', { name: 'Search versions' })).toHaveValue('')
  fireEvent.click(screen.getByRole('button', { name: 'Close' }))
  expect(screen.getByText('Saved canvas 1')).toBeInTheDocument()

  fireEvent.click(screen.getByRole('button', { name: 'Return to experiment' }))
  await act(async () => {
    client.setQueryData(['protocols', 'for-experiment', 'e'], { ...protocol, has_unpublished_changes: true })
  })
  const publish = vi.spyOn(protocolsApi, 'publish').mockResolvedValue({ ...protocol, published_revision: 10, published_revision_id: 'v10', has_unpublished_changes: false })
  fireEvent.click(await screen.findByRole('button', { name: 'Publish experiment' }))
  const dialog = await screen.findByRole('dialog', { name: 'Publish a new experiment version?' })
  expect(publish).not.toHaveBeenCalled()
  fireEvent.change(screen.getByRole('textbox', { name: 'Version name (optional)' }), { target: { value: 'Higher threshold' } })
  fireEvent.change(screen.getByRole('textbox', { name: 'Version note (optional)' }), { target: { value: 'Reduce false positives' } })
  fireEvent.click(within(dialog).getByRole('button', { name: 'Publish experiment' }))
  await waitFor(() => expect(publish).toHaveBeenCalledWith('p', { name: 'Higher threshold', note: 'Reduce false positives' }))
})
