import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { packageImportsApi } from '@/api/client'
import { ImportedExperimentSetup } from './ImportedExperimentSetup'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

it('keeps incomplete drafts accessible and requires explicit connection selection', async () => {
  const options = { ready: false, issues: [{ node_id: 'tool', message: 'Choose a local connection' }],
    providers: [{ provider: 'openai', id: 'saved-provider', selected: false }],
    mcp: [{ source_id: 'source', name: 'Custom server', selected_id: null,
      candidates: [{ id: 'one', name: 'Connection one' }, { id: 'two', name: 'Connection two' }] }] }
  vi.spyOn(packageImportsApi, 'readiness').mockResolvedValue(options)
  const select = vi.spyOn(packageImportsApi, 'selectConnection').mockResolvedValue(options)
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const onSelectNode = vi.fn()
  render(<QueryClientProvider client={client}><MemoryRouter>
    <ImportedExperimentSetup importId="import" experimentId="experiment" onSelectNode={onSelectNode} />
  </MemoryRouter></QueryClientProvider>)
  expect(await screen.findByText('Setup needed')).toBeInTheDocument()
  expect(select).not.toHaveBeenCalled()
  expect(screen.getByText(/Multiple compatible connections/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Choose a local connection' }))
  expect(onSelectNode).toHaveBeenCalledWith('tool')
  fireEvent.change(screen.getByRole('combobox'), { target: { value: 'two' } })
  await waitFor(() => expect(select).toHaveBeenCalledWith('import', 'mcp', 'source', 'two'))
  await waitFor(() => expect(screen.getByRole('button', { name: 'Use my saved connection' })).toBeEnabled())
  fireEvent.click(screen.getByRole('button', { name: 'Use my saved connection' }))
  await waitFor(() => expect(select).toHaveBeenCalledWith('import', 'provider', 'openai', 'saved-provider'))
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
})
