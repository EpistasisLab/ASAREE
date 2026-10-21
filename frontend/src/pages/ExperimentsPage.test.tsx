import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi } from '@/api/client'
import type { Experiment } from '@/types/experiments'
import type { ExecutionSummary } from '@/types/executionResults'
import { ExperimentsPage } from './ExperimentsPage'

vi.mock('@/components/AppHeader', () => ({ AppHeader: () => null }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

function setup() {
  const experiments = Array.from({ length: 11 }, (_, i) => ({
    id: `experiment-${i}`,
    name: `Study ${i}`,
    description: 'Model comparison',
    design_type: i === 10 ? 'fractional' : 'factorial',
    design_spec: null,
    created_at: new Date(2026, 0, 12 - i).toISOString(),
    archived_at: null,
  } as Experiment))
  vi.spyOn(experimentsApi, 'list').mockResolvedValue(experiments)
  vi.spyOn(experimentsApi, 'getExecutionSummary').mockImplementation(async (id) => ({
    ready: true,
    total: 2,
    scored_count: id === 'experiment-10' ? 2 : 0,
  } as ExecutionSummary))
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><ExperimentsPage /></MemoryRouter></QueryClientProvider>)
}

it('filters progress across pages and combines it with design and search', async () => {
  const user = userEvent.setup()
  setup()
  await screen.findByText('Study 0')
  expect(screen.queryByText('Study 10')).not.toBeInTheDocument()
  await user.click(screen.getByRole('combobox', { name: 'Filter by progress' }))
  await user.click(await screen.findByRole('option', { name: 'Fully scored' }))
  expect(await screen.findByText('Study 10')).toBeInTheDocument()
  expect(screen.queryByText('Study 0')).not.toBeInTheDocument()
  expect(screen.getByText('Page 1 of 1')).toBeInTheDocument()
  await user.click(screen.getByRole('combobox', { name: 'Filter by design type' }))
  await user.click(await screen.findByRole('option', { name: 'factorial' }))
  expect(await screen.findByText('No experiments match your search or filters.')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }))
  expect(await screen.findByText('Study 0')).toBeInTheDocument()
  fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'Study 10' } })
  expect(await screen.findByText('Study 10')).toBeInTheDocument()
  expect(screen.queryByText('Study 0')).not.toBeInTheDocument()
})

it('does not classify failed summaries as experiments with no cells', async () => {
  const user = userEvent.setup()
  setup()
  vi.mocked(experimentsApi.getExecutionSummary).mockRejectedValue(new Error('Unavailable'))
  await screen.findByText('Study 0')
  await user.click(screen.getByRole('combobox', { name: 'Filter by progress' }))
  await user.click(await screen.findByRole('option', { name: 'No cells' }))
  expect(await screen.findByRole('alert')).toHaveTextContent('Some progress could not be loaded')
  expect(screen.queryByText('Study 10')).not.toBeInTheDocument()
})
