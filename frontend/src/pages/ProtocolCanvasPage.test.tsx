import { useState } from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen } from '@testing-library/react'
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
vi.mock('@/components/protocol/ExperimentSidePanel', () => ({ ExperimentSidePanel: (props: { versionControls: React.ReactNode; viewingHistory: boolean; version?: ProtocolRevision; draftExperiment: Experiment; resultsExperiment: Experiment }) => <div>{props.versionControls}<p>{props.viewingHistory ? `Historical design: ${props.version?.experiment_snapshot?.hypothesis}` : `Draft design: ${props.draftExperiment?.hypothesis}`}</p><p>Results design: {props.resultsExperiment?.hypothesis}</p></div> }))
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
  vi.spyOn(protocolsApi, 'listRevisions').mockResolvedValue(versions)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/experiments/e/protocol']}><Routes><Route path="/experiments/:experimentId/protocol" element={<ProtocolCanvasPage />} /></Routes></MemoryRouter></QueryClientProvider>)
  const draft = await screen.findByRole('textbox', { name: 'Draft canvas' })
  await screen.findByText('Results design: Published')
  fireEvent.change(draft, { target: { value: 'Local edit' } })
  fireEvent.change(screen.getByRole('combobox', { name: 'Experiment version' }), { target: { value: 'v1' } })
  expect(screen.getByText('Saved canvas 1')).toBeInTheDocument()
  expect(screen.getByText('Historical design: Original')).toBeInTheDocument()
  expect(screen.getByText('Results design: Original')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'Publish experiment' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Return to draft' }))
  expect(screen.getByRole('textbox', { name: 'Draft canvas' })).toHaveValue('Local edit')
  expect(screen.getByText('Draft design: Current')).toBeInTheDocument()
  expect(screen.getByText('Results design: Published')).toBeInTheDocument()
})
