import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterAll, afterEach, beforeAll, expect, it, vi } from 'vitest'
import { packageImportsApi } from '@/api/client'
import type { PackageImport } from '@/types/packageImports'
import { ExperimentPackageImport } from './ExperimentPackageImport'

afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals() })

// CodeMirror measures text ranges; jsdom provides no layout implementation.
const rangeRect = Object.getOwnPropertyDescriptor(Range.prototype, 'getBoundingClientRect')
const rangeRects = Object.getOwnPropertyDescriptor(Range.prototype, 'getClientRects')
beforeAll(() => {
  Object.defineProperties(Range.prototype, {
    getBoundingClientRect: { configurable: true, value: () => new DOMRect() },
    getClientRects: { configurable: true, value: () => [] },
  })
})
afterAll(() => {
  for (const [name, descriptor] of [['getBoundingClientRect', rangeRect], ['getClientRects', rangeRects]] as const) {
    if (descriptor) Object.defineProperty(Range.prototype, name, descriptor)
    else Reflect.deleteProperty(Range.prototype, name)
  }
})

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
  vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) })
  const create = vi.spyOn(packageImportsApi, 'create').mockResolvedValue({ ...ready, status: 'uploading', preflight: {} })
  const upload = vi.spyOn(packageImportsApi, 'upload').mockResolvedValue({ ...ready, status: 'validating' })
  const command = vi.spyOn(packageImportsApi, 'command').mockResolvedValue({ ...ready, status: 'queued' })
  show()
  const file = new File(['archive bytes'], 'package.tar.gz', { type: 'application/gzip' })
  const input = screen.getByLabelText<HTMLInputElement>('Experiment package')
  expect(input.accept.split(',')).toEqual(expect.arrayContaining(['.gz', 'application/gzip']))
  fireEvent.change(input, { target: { files: [file] } })
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
  fireEvent.click(await screen.findByRole('button', { name: /request.*Ready to review/ }))
  fireEvent.click(await screen.findByRole('button', { name: 'Cancel import' }))
  await waitFor(() => expect(command).toHaveBeenCalledWith('request', 'cancel'))
})

it('drops a package into the existing inspection flow without confirming import', async () => {
  const create = vi.spyOn(packageImportsApi, 'create').mockResolvedValue({ ...ready, status: 'uploading', preflight: {} })
  const upload = vi.spyOn(packageImportsApi, 'upload').mockResolvedValue({ ...ready, status: 'validating' })
  const command = vi.spyOn(packageImportsApi, 'command')
  show()
  const file = new File(['archive bytes'], 'experiment.tgz', { type: 'application/gzip' })
  const target = screen.getByText('Drop a .tar.gz or .tgz package or click to browse').closest('label')!
  fireEvent.dragOver(target)
  fireEvent.drop(target, { dataTransfer: { files: [file] } })
  await waitFor(() => expect(upload).toHaveBeenCalledWith('request', file, expect.any(AbortSignal)))
  expect(create).toHaveBeenCalledOnce()
  expect(command).not.toHaveBeenCalled()
  expect(screen.getByText('experiment.tgz')).toBeInTheDocument()
  expect(await screen.findByText(/Source version 7/)).toBeInTheDocument()
})

it('prevents additional drops and file selection while uploading', async () => {
  const create = vi.spyOn(packageImportsApi, 'create').mockResolvedValue({ ...ready, status: 'uploading', preflight: {} })
  const upload = vi.spyOn(packageImportsApi, 'upload').mockReturnValue(new Promise(() => {}))
  show()
  const input = screen.getByLabelText('Experiment package')
  const target = input.closest('label')!
  const file = new File(['archive bytes'], 'experiment.tar.gz')
  fireEvent.drop(target, { dataTransfer: { files: [file] } })
  await waitFor(() => expect(upload).toHaveBeenCalledOnce())
  expect(input).toBeDisabled()
  fireEvent.drop(target, { dataTransfer: { files: [new File(['other'], 'other.tgz')] } })
  expect(create).toHaveBeenCalledOnce()
  expect(upload).toHaveBeenCalledOnce()
})

it('shows readiness and a direct draft link without an acknowledgement gate', async () => {
  const complete = { ...ready, status: 'completed', experiment_id: 'local-experiment' }
  vi.spyOn(packageImportsApi, 'readiness').mockResolvedValue({ ready: true, issues: [], mcp: [], providers: [] })
  show([complete])
  vi.mocked(packageImportsApi.get).mockResolvedValue(complete)
  fireEvent.click(await screen.findByRole('button', { name: /request.*Imported/ }))
  expect(await screen.findByText('Ready to publish')).toBeInTheDocument()
  expect(screen.getByRole('link', { name: 'Open editable draft' })).toHaveAttribute('href', '/experiments/local-experiment/protocol')
  expect(screen.queryByRole('checkbox')).not.toBeInTheDocument()
})

it('inspects immutable observation evidence without coercing null or unavailable values', async () => {
  show([ready])
  const path = 'results/attempts/source-attempt/attempt_result.json'
  vi.mocked(packageImportsApi.files).mockResolvedValue({ items: [{ path, sha256: 'exact-hash', bytes: 200,
    source_id: 'source-attempt', provenance: 'recorded_attempt' }], next_offset: null })
  const source = '{"observations":[{"status":"measured","value":null},{"status":"unavailable"}]}'
  vi.spyOn(packageImportsApi, 'source').mockResolvedValue(new Blob([source]))
  fireEvent.click(await screen.findByRole('button', { name: /request.*Ready to review/ }))
  fireEvent.click(await screen.findByText('Inspect source files and historical evidence'))
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect JSON' }))
  const viewer = await screen.findByRole('textbox', { name: 'JSON source' })
  expect(viewer).toHaveAttribute('aria-readonly', 'true')
  expect(viewer).toHaveTextContent('"status": "measured"')
  expect(viewer).toHaveTextContent('"value": null')
  expect(viewer).toHaveTextContent('"status": "unavailable"')
  fireEvent.click(screen.getByRole('button', { name: 'Original' }))
  await waitFor(() => expect(viewer.textContent).toBe(source))
  expect(packageImportsApi.source).toHaveBeenCalledWith('request', path)
})

it('opens JSON inspection immediately while the file request is pending', async () => {
  show([ready])
  const path = 'publication/settings.json'
  vi.mocked(packageImportsApi.files).mockResolvedValue({ items: [{ path, sha256: 'hash', bytes: 100,
    source_id: 'publication', provenance: 'publication_pinned' }], next_offset: null })
  let finish!: (value: Response) => void
  const fetchFile = vi.spyOn(globalThis, 'fetch').mockImplementation(() => new Promise(resolve => { finish = resolve }))
  fireEvent.click(await screen.findByRole('button', { name: /request.*Ready to review/ }))
  fireEvent.click(await screen.findByText('Inspect source files and historical evidence'))
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect JSON' }))
  const dialog = await screen.findByRole('dialog', { name: 'Inspect JSON' })
  expect(within(dialog).getByRole('status')).toHaveTextContent('Loading JSON')
  expect(within(dialog).getByText(path)).toBeInTheDocument()
  await waitFor(() => expect(fetchFile).toHaveBeenCalledWith(
    '/api/experiment-package-imports/request/source?path=publication%2Fsettings.json', expect.anything()))
  const source = '{"exact_integer":9007199254740993,"value":null}'
  await act(async () => { finish(new Response(source, { headers: { 'Content-Type': 'application/octet-stream' } })) })
  expect(await within(dialog).findByRole('textbox', { name: 'JSON source' })).toHaveTextContent('9007199254740993')
})

it('shows source-loading errors in the inspector and allows retry', async () => {
  show([ready])
  const path = 'publication/settings.json'
  vi.mocked(packageImportsApi.files).mockResolvedValue({ items: [{ path, sha256: 'hash', bytes: 100,
    source_id: 'publication', provenance: 'publication_pinned' }], next_offset: null })
  const source = vi.spyOn(packageImportsApi, 'source').mockRejectedValueOnce(new Error('Source file unavailable'))
    .mockResolvedValue(new Blob(['{"restored":true}']))
  fireEvent.click(await screen.findByRole('button', { name: /request.*Ready to review/ }))
  fireEvent.click(await screen.findByText('Inspect source files and historical evidence'))
  fireEvent.click(await screen.findByRole('button', { name: 'Inspect JSON' }))
  const dialog = await screen.findByRole('dialog', { name: 'Inspect JSON' })
  expect(await within(dialog).findByRole('alert')).toHaveTextContent('Source file unavailable')
  fireEvent.click(within(dialog).getByRole('button', { name: 'Retry loading file' }))
  expect(await within(dialog).findByRole('textbox', { name: 'JSON source' })).toHaveTextContent('"restored": true')
  expect(source).toHaveBeenCalledTimes(2)
  expect(within(dialog).queryByRole('alert')).not.toBeInTheDocument()
})

it('keeps a late response from reopening a closed inspector or replacing a newer file', async () => {
  show([ready])
  vi.mocked(packageImportsApi.files).mockResolvedValue({ items: ['first.json', 'second.json'].map(path => ({
    path, sha256: 'hash', bytes: 100, source_id: path, provenance: 'publication_pinned',
  })), next_offset: null })
  let finishFirst!: (value: Blob) => void
  vi.spyOn(packageImportsApi, 'source').mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }))
    .mockResolvedValue(new Blob(['{"file":"second"}']))
  fireEvent.click(await screen.findByRole('button', { name: /request.*Ready to review/ }))
  fireEvent.click(await screen.findByText('Inspect source files and historical evidence'))
  fireEvent.click((await screen.findAllByRole('button', { name: 'Inspect JSON' }))[0])
  const first = await screen.findByRole('dialog', { name: 'Inspect JSON' })
  fireEvent.click(within(first).getByRole('button', { name: 'Close source inspection' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  fireEvent.click(screen.getAllByRole('button', { name: 'Inspect JSON' })[1])
  const second = await screen.findByRole('dialog', { name: 'Inspect JSON' })
  const viewer = await within(second).findByRole('textbox', { name: 'JSON source' })
  expect(viewer).toHaveTextContent('"file": "second"')
  await act(async () => { finishFirst(new Blob(['{"file":"first"}'])) })
  expect(within(second).getByText('second.json')).toBeInTheDocument()
  expect(viewer).toHaveTextContent('"file": "second"')
  fireEvent.click(within(second).getByRole('button', { name: 'Close source inspection' }))
  await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
})
