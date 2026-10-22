import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { experimentsApi, statsApi } from '@/api/client'
import type { StatsAnalysis, StatsConfig } from '@/types/stats'
import { StatsPrototypePage } from './StatsPrototypePage'

vi.mock('@/components/AppHeader', () => ({ AppHeader: () => null }))

const config: StatsConfig = {
  metric: 'accuracy', factors: ['model'], family: 'gaussian', measurement_type: 'continuous',
  denominator: null, ordinal_levels: [], max_order: 1, n_boot: 100, n_boot_lrt: 0,
  n_perm: 999, seed: 72, tweedie_power: 1.5,
}
const snapshot: StatsAnalysis = {
  id: 'snapshot', backend: 'stats', status: 'completed', error: null,
  created_at: '2026-10-22T00:00:00Z', config, fingerprint: 'same-input', design_revision: 1,
  source: { factors: [{ name: 'model', levels: ['a', 'b'] }], metrics: [{ name: 'accuracy', n: 12 }],
    n_replicates: 12, design_revision: 1, fingerprint: 'same-input', excluded_row_replicates: 0 },
}

beforeEach(() => {
  vi.spyOn(experimentsApi, 'list').mockResolvedValue([])
  vi.spyOn(statsApi, 'source').mockResolvedValue({ factors: [{ name: 'model', levels: ['a', 'b'] }],
    metrics: [{ name: 'accuracy', n: 20 }], n_replicates: 20, design_revision: 1,
    fingerprint: 'live-input', excluded_row_replicates: 0 })
  vi.spyOn(statsApi, 'history').mockResolvedValue([])
  vi.spyOn(statsApi, 'capabilities').mockResolvedValue({ families: ['gaussian'], unavailable: [
    { title: 'ART / ART-C', reason: 'No established implementation.', reference: '/stats' },
  ] })
})
afterEach(() => { cleanup(); vi.restoreAllMocks() })

function mount(path = '/stats_sm') {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter initialEntries={[path]}>
    <StatsPrototypePage backend="stats_sm" />
  </MemoryRouter></QueryClientProvider>)
  return client
}

it('shows unmapped features and the original reference before an analysis is run', async () => {
  mount()
  expect(await screen.findByText('ART / ART-C')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Reference: original /stats implementation' })).toHaveAttribute('href', '/stats')
})

it('preserves an unsupported snapshot family instead of silently changing models', async () => {
  vi.spyOn(statsApi, 'get').mockResolvedValue({ ...snapshot, config: { ...config, family: 'zoib' } })
  mount('/stats_sm?experiment=experiment&source=snapshot')
  await waitFor(() => expect(screen.getByLabelText('Model family')).toHaveValue('zoib'))
  expect(screen.getByRole('button', { name: 'Run and save analysis' })).toBeDisabled()
  expect(screen.getByText(/No mapping available for zoib/)).toBeInTheDocument()
})

it('compares the saved input and configuration even when live data has changed', async () => {
  vi.spyOn(statsApi, 'get').mockImplementation(async (_experiment, id) => id === 'snapshot' ? snapshot : {
    ...snapshot, id, backend: 'stats_sm', status: 'pending',
  })
  const run = vi.spyOn(statsApi, 'run').mockResolvedValue({ ...snapshot, id: 'new', backend: 'stats_sm', status: 'pending' })
  const client = mount('/stats_sm?experiment=experiment&source=snapshot')
  await waitFor(() => expect(screen.getByLabelText('Outcome metric')).toHaveValue('accuracy'))
  expect(screen.getByRole('option', { name: 'accuracy (12 measured)' })).toBeInTheDocument()
  expect(statsApi.source).not.toHaveBeenCalled()
  fireEvent.click(screen.getByRole('button', { name: 'Run and save analysis' }))
  await waitFor(() => expect(run).toHaveBeenCalledWith('experiment', 'stats_sm', config, 'snapshot'))
  client.clear()
})
