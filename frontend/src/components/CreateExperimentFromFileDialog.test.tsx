import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { experimentsApi, packageImportsApi } from '@/api/client'
import type { Experiment } from '@/types/experiments'
import type { PackageImport } from '@/types/packageImports'
import { CreateExperimentFromFileDialog } from './CreateExperimentFromFileDialog'

afterEach(() => vi.restoreAllMocks())

function show() {
  const imported = vi.fn()
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><CreateExperimentFromFileDialog open onOpenChange={vi.fn()} onImported={imported} /></QueryClientProvider>)
  return { imported, client }
}

it('keeps legacy JSON definitions and creates their draft without restoring a publication', async () => {
  const create = vi.spyOn(experimentsApi, 'importDefinition').mockResolvedValue({ id: 'new' } as Experiment)
  const { imported } = show()
  const definition = { name: 'Old experiment', graph: { nodes: [], edges: [] },
    canvas: { published: { graph: { nodes: [{ id: 'historical' }], edges: [] } } } }
  const file = new File([JSON.stringify(definition)], 'experiment.json', { type: 'application/json' })
  fireEvent.change(screen.getByLabelText('Experiment definition'), { target: { files: [file] } })
  fireEvent.click(await screen.findByRole('button', { name: 'Create experiment' }))
  await waitFor(() => expect(imported).toHaveBeenCalledWith('new'))
  expect(create).toHaveBeenCalledWith(expect.objectContaining({ name: 'Old experiment', graph: definition.graph }))
  expect(create.mock.calls[0][0]).not.toHaveProperty('published_graph')
})

it.each(['experiment.zip', 'experiment.ZIP'])('uploads %s and opens its draft automatically', async (filename) => {
  const job = { id: 'import', status: 'completed', experiment_id: 'restored', progress: {}, preflight: {} } as PackageImport
  vi.spyOn(packageImportsApi, 'create').mockResolvedValue(job)
  const upload = vi.spyOn(packageImportsApi, 'upload').mockResolvedValue(job)
  vi.spyOn(packageImportsApi, 'get').mockResolvedValue(job)
  const command = vi.spyOn(packageImportsApi, 'command')
  const { imported } = show()
  const file = new File(['archive bytes'], filename, { type: 'application/zip' })
  const input = screen.getByLabelText('Experiment definition')
  expect(input).toHaveAttribute('accept', 'application/json,.json,application/zip,.zip')
  fireEvent.change(input, { target: { files: [file] } })
  fireEvent.click(screen.getByRole('button', { name: 'Create experiment' }))
  await waitFor(() => expect(imported).toHaveBeenCalledWith('restored'))
  expect(upload).toHaveBeenCalledWith('import', file)
  expect(command).not.toHaveBeenCalled()
  expect(screen.queryByText(/Confirm import/)).not.toBeInTheDocument()
})

it.each(['Hash mismatch', 'Could not import 2 resources:\n• Could not register skill "Taken skill": already registered\n• Could not register dataset "Invalid dataset": invalid target'])('reports all import errors without navigating to a partial experiment: %s', async (error) => {
  const job = { id: 'import', status: 'failed', experiment_id: null, error, progress: {}, preflight: {} } as PackageImport
  vi.spyOn(packageImportsApi, 'create').mockResolvedValue(job)
  vi.spyOn(packageImportsApi, 'upload').mockResolvedValue(job)
  vi.spyOn(packageImportsApi, 'get').mockResolvedValue(job)
  const { imported } = show()
  fireEvent.change(screen.getByLabelText('Experiment definition'), { target: { files: [new File(['x'], 'bad.zip')] } })
  fireEvent.click(screen.getByRole('button', { name: 'Create experiment' }))
  await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent(error.replace(/\s+/g, ' ')))
  expect(imported).not.toHaveBeenCalled()
  expect(screen.getByRole('button', { name: 'Retry import' })).toBeInTheDocument()
})
