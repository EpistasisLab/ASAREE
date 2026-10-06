import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { protocolsApi } from '@/api/client'
import type { ProtocolRevision } from '@/types/protocols'
import { ExperimentVersionHistory } from './ExperimentVersionHistory'

afterEach(() => vi.restoreAllMocks())

const versions: ProtocolRevision[] = [3, 2, 1].map(revision => ({
  id: `v${revision}`, protocol_id: 'p', revision, graph: { nodes: [], edges: [] },
  published_at: `2026-01-0${revision}`, name: revision === 1 ? 'Baseline' : null,
  note: revision === 1 ? 'Lower critic threshold' : null,
  run_count: revision === 2 ? 2 : 0, result_count: revision === 2 ? 1 : 0,
}))

it('filters by provenance counts and searches notes while keeping production visible', () => {
  const client = new QueryClient()
  const onSelect = vi.fn()
  const props = { versions, protocolId: 'p', publishedId: 'v3', selectedId: '', onSearch: vi.fn(), onSelect }
  const { rerender } = render(<QueryClientProvider client={client}><ExperimentVersionHistory {...props} search="" /></QueryClientProvider>)
  fireEvent.click(screen.getByRole('button', { name: 'With results' }))
  expect(screen.getByRole('button', { name: 'View Version 3' })).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'View Version 2' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'View Version 1 · Baseline' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'With runs' }))
  expect(screen.getByText('1 of 3 versions · newest first')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'All' }))
  rerender(<QueryClientProvider client={client}><ExperimentVersionHistory {...props} search="critic" /></QueryClientProvider>)
  expect(screen.getByRole('button', { name: 'View Version 1 · Baseline' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: 'View Version 2' })).not.toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'View Version 1 · Baseline' }))
  expect(onSelect).toHaveBeenCalledWith('v1')
})

it('edits annotations and updates the cached version without changing its definition', async () => {
  const client = new QueryClient()
  client.setQueryData(['protocols', 'p', 'revisions'], versions)
  const updated = { ...versions[2], name: 'Reference', note: 'Keep for comparison' }
  const save = vi.spyOn(protocolsApi, 'updateRevision').mockResolvedValue(updated)
  render(<QueryClientProvider client={client}><ExperimentVersionHistory versions={versions} protocolId="p" selectedId="" search="" onSearch={vi.fn()} onSelect={vi.fn()} /></QueryClientProvider>)
  const entry = screen.getByRole('button', { name: 'View Version 1 · Baseline' }).closest('[data-slot="card"]') as HTMLElement
  fireEvent.click(within(entry).getByRole('button', { name: 'Details' }))
  expect(within(entry).getByText('Lower critic threshold')).toBeInTheDocument()
  fireEvent.click(within(entry).getByRole('button', { name: 'Edit name and note' }))
  fireEvent.change(within(entry).getByRole('textbox', { name: 'Version name (optional)' }), { target: { value: ' Reference ' } })
  fireEvent.change(within(entry).getByRole('textbox', { name: 'Version note (optional)' }), { target: { value: 'Keep for comparison' } })
  fireEvent.click(within(entry).getByRole('button', { name: 'Save details' }))
  await waitFor(() => expect(save).toHaveBeenCalledWith('p', 'v1', { name: 'Reference', note: 'Keep for comparison' }))
  await waitFor(() => expect(client.getQueryData<ProtocolRevision[]>(['protocols', 'p', 'revisions'])?.[2]).toEqual(updated))
})
