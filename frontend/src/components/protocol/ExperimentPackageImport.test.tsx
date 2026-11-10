import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { packageImportsApi } from '@/api/client'
import type { PackageImport } from '@/types/packageImports'
import { ExperimentPackageImport } from './ExperimentPackageImport'

afterEach(() => { cleanup(); vi.restoreAllMocks() })

const ready: PackageImport = { id: 'request', status: 'awaiting_confirmation', sha256: 'source-hash',
  experiment_id: null, configured_at: null, created_at: '', expires_at: '', error: null,
  progress: { phase: 'preflight_ready' }, preflight: { manifest: { schema_version: 2,
    experiment_id: 'source', publication: { id: 'publication', version: 7, published_at: '' }, results_included: true },
    categories: { inputs: 4, results: 12 }, unresolved_count: 1, external_requirements: 2,
    warnings: ['Configure local credentials before publication.'],
    diagnostics: [{ id: 'missing', kind: 'dataset_file', status: 'missing', reason: 'Pinned file unavailable' }] } }

function show(items: PackageImport[] = []) {
  vi.spyOn(packageImportsApi, 'list').mockResolvedValue(items)
  vi.spyOn(packageImportsApi, 'get').mockResolvedValue(ready)
  vi.spyOn(packageImportsApi, 'files').mockResolvedValue({ items: [], next_offset: null })
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><MemoryRouter><ExperimentPackageImport /></MemoryRouter></QueryClientProvider>)
}

it('uploads for inspection and requires an explicit confirmation after showing requirements', async () => {
  const create = vi.spyOn(packageImportsApi, 'create').mockResolvedValue({ ...ready, status: 'uploading', preflight: {} })
  const upload = vi.spyOn(packageImportsApi, 'upload').mockResolvedValue({ ...ready, status: 'validating' })
  const command = vi.spyOn(packageImportsApi, 'command').mockResolvedValue({ ...ready, status: 'queued' })
  show()
  const file = new File(['archive bytes'], 'package.tar.gz', { type: 'application/gzip' })
  fireEvent.change(screen.getByLabelText('Experiment package'), { target: { files: [file] } })
  await waitFor(() => expect(upload).toHaveBeenCalledWith('request', file, expect.any(AbortSignal)))
  expect(create).toHaveBeenCalledOnce()
  expect(command).not.toHaveBeenCalled()
  expect(await screen.findByText(/Source version 7/)).toBeInTheDocument()
  expect(screen.getByText(/Pinned file unavailable/)).toBeInTheDocument()
  expect(screen.getByText(/Configure local credentials/)).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Confirm import into a new experiment' }))
  await waitFor(() => expect(command).toHaveBeenCalledWith('request', 'confirm'))
})

it('resumes persisted requests and allows cancellation before activation', async () => {
  const command = vi.spyOn(packageImportsApi, 'command').mockResolvedValue({ ...ready, status: 'cancelled' })
  show([ready])
  fireEvent.click(await screen.findByRole('button', { name: /request.*awaiting_confirmation/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel import' }))
  await waitFor(() => expect(command).toHaveBeenCalledWith('request', 'cancel'))
})

it('keeps source history and local-configuration acknowledgement distinct', async () => {
  const complete = { ...ready, status: 'completed', experiment_id: 'local-experiment' }
  const configure = vi.spyOn(packageImportsApi, 'configure').mockResolvedValue(complete)
  show([complete])
  vi.mocked(packageImportsApi.get).mockResolvedValue(complete)
  fireEvent.click(await screen.findByRole('button', { name: /request.*completed/ }))
  const acknowledge = await screen.findByRole('button', { name: 'Acknowledge local configuration' })
  expect(acknowledge).toBeDisabled()
  expect(screen.getByRole('link', { name: 'Open editable draft' })).toHaveAttribute('href', '/experiments/local-experiment/protocol')
  fireEvent.click(screen.getByRole('checkbox'))
  fireEvent.click(acknowledge)
  await waitFor(() => expect(configure).toHaveBeenCalledWith('request'))
})
