import { useId, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { ChevronDown, Download, File, FileJson, Search } from 'lucide-react'
import { packageImportsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Badge } from '@/components/ui/badge'
import type { PackageImport } from '@/types/packageImports'
import { newRequestKey } from '@/lib/requestKey'
import { FileDropInput } from './FileDropInput'
import { JsonSourceViewer } from './JsonSourceViewer'
import { ImportedExperimentSetup } from './ImportedExperimentSetup'

const provenanceLabels: Record<string, string> = {
  publication_pinned: 'Published snapshot',
  captured_at_download: 'Captured at export',
  recorded_attempt: 'Recorded attempt',
  retained_attempt_artifact: 'Retained artifact',
  'legacy.unknown': 'Unknown historical source',
}

function fileSize(bytes: number): string {
  if (bytes < 1024) return `${bytes.toLocaleString()} B`
  const unit = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), 3)
  return `${(bytes / 1024 ** unit).toLocaleString(undefined, { maximumFractionDigits: 1 })} ${['B', 'KiB', 'MiB', 'GiB'][unit]}`
}

const categoryLabels: Record<string, string> = {
  inputs: 'Dataset files and metadata',
  resources: 'Scripts, skills, and knowledge',
  publication: 'Published configuration',
  requirements: 'Local requirements',
  results: 'Historical results',
  design: 'Experimental design',
  sources: 'Imported source evidence',
  'dependencies.jsonl': 'Dependency inventory',
  'experiment-metadata.json': 'Experiment details',
}

const statusLabels: Record<string, string> = {
  uploading: 'Uploading package',
  validating: 'Checking package',
  awaiting_confirmation: 'Ready to review',
  queued: 'Import queued',
  activating: 'Creating experiment',
  completed: 'Imported',
  failed: 'Import failed',
  cancelled: 'Cancelled',
  expired: 'Expired',
}

function PackageSummary({ current }: { current: PackageImport }) {
  const { manifest, categories, unresolved_count: unresolved, external_requirements: external } = current.preflight
  if (!manifest) return null
  const contents = Object.entries(categories ?? {})
  return <section aria-label="Package summary" className="space-y-4 rounded-lg border bg-muted/20 p-4">
    <div>
      <h3 className="text-sm font-medium">Package summary</h3>
      <p className="text-xs text-muted-foreground">{current.status === 'awaiting_confirmation'
        ? 'Review the contents, then confirm to create a new editable experiment.'
        : 'Contents of the uploaded source package.'}</p>
    </div>
    <dl className="grid grid-cols-2 gap-3 text-sm">
      <div><dt className="text-xs text-muted-foreground">Published version</dt><dd className="font-mono">Source version {manifest.publication.version}</dd>
        {manifest.publication.name && <dd className="break-words text-xs text-muted-foreground">{manifest.publication.name}</dd>}</div>
      <div><dt className="text-xs text-muted-foreground">Historical results</dt><dd>{manifest.results_included ? 'Included' : 'Not included'}</dd></div>
    </dl>
    {contents.length > 0 && <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-left text-sm">
        <caption className="sr-only">Included package files by category</caption>
        <thead className="bg-muted/40 text-xs text-muted-foreground"><tr><th scope="col" className="px-3 py-2 font-medium">Contents</th><th scope="col" className="px-3 py-2 text-right font-medium">Files</th></tr></thead>
        <tbody className="divide-y">{contents.map(([name, count]) => <tr key={name}>
          <th scope="row" className="px-3 py-2 font-normal">{categoryLabels[name] ?? name}</th>
          <td className="px-3 py-2 text-right font-mono tabular-nums">{count.toLocaleString()}</td>
        </tr>)}</tbody>
        <tfoot className="border-t bg-muted/30"><tr><th scope="row" className="px-3 py-2 font-medium">Total files</th><td className="px-3 py-2 text-right font-mono tabular-nums">{contents.reduce((total, [, count]) => total + count, 0).toLocaleString()}</td></tr></tfoot>
      </table>
    </div>}
    <div className="space-y-1 rounded-md border p-3 text-sm">
      {unresolved !== undefined && <p className={unresolved > 0 ? 'text-amber-400' : 'text-muted-foreground'}>{unresolved > 0
        ? `${unresolved.toLocaleString()} unresolved ${unresolved === 1 ? 'dependency needs' : 'dependencies need'} attention`
        : 'No unresolved dependencies reported'}</p>}
      {external !== undefined && <p>{external.toLocaleString()} external {external === 1 ? 'requirement' : 'requirements'}</p>}
      <p className="text-xs text-muted-foreground">After import, configure local connections and resolve missing dependencies in the draft before publishing. Import does not run scripts or tools.</p>
    </div>
    <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">Package identifiers and format</summary>
      <dl className="mt-2 space-y-2"><div><dt>Package schema</dt><dd className="font-mono">{manifest.schema_version}</dd></div>
        <div><dt>Source publication</dt><dd className="break-all font-mono">{manifest.publication.id}</dd></div>
        <div><dt>Archive SHA-256</dt><dd className="break-all font-mono">{current.sha256 ?? 'Unavailable'}</dd></div></dl>
    </details>
  </section>
}

/** Persistent imports can be resumed after navigation or a browser restart. */
export function ExperimentPackageImport({ experimentId }: { experimentId?: string }) {
  const client = useQueryClient()
  const fileInputId = useId()
  const [file, setFile] = useState<File | null>(null)
  const [selected, setSelected] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [preview, setPreview] = useState<{ importId: string; path: string; text?: string; error?: string; loading: boolean } | null>(null)
  const previewRequest = useRef(0)
  const selectedRef = useRef(selected)
  selectedRef.current = selected
  const uploadController = useRef<AbortController | null>(null)
  const list = useQuery({ queryKey: ['package-imports', experimentId], queryFn: () => packageImportsApi.list(experimentId),
    refetchInterval: (query) => query.state.data?.some((item) => ['uploading', 'validating', 'queued', 'activating'].includes(item.status)) ? 3000 : false })
  const job = useQuery({ queryKey: ['package-import', selected], enabled: selected !== null,
    queryFn: () => packageImportsApi.get(selected!),
    refetchInterval: (query) => !query.state.data || ['uploading', 'validating', 'queued', 'activating'].includes(query.state.data.status) ? 1000 : false })
  const current = job.data
  const files = useQuery({ queryKey: ['package-import-files', selected, offset],
    enabled: !!current?.preflight.manifest && !['expired', 'cancelled'].includes(current.status),
    queryFn: () => packageImportsApi.files(selected!, offset) })
  const action = useMutation({
    mutationFn: async ({ id, command }: { id: string; command: 'confirm' | 'cancel' | 'retry' }) => packageImportsApi.command(id, command),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['package-import'] })
      void client.invalidateQueries({ queryKey: ['package-imports'] })
      void client.invalidateQueries({ queryKey: ['experiments'] })
    },
    onError: (cause) => setError(String(cause)),
  })
  const upload = async (file: File) => {
    setError(null)
    setUploading(true)
    try {
      const created = current && ['uploading', 'failed'].includes(current.status) && !current.preflight.manifest
        ? current : await packageImportsApi.create(newRequestKey())
      setSelected(created.id)
      setOffset(0)
      const controller = new AbortController()
      uploadController.current = controller
      await packageImportsApi.upload(created.id, file, controller.signal)
      void client.invalidateQueries({ queryKey: ['package-imports'] })
    } catch (cause) {
      if (!(cause instanceof DOMException && cause.name === 'AbortError')) setError(String(cause))
    }
    finally { uploadController.current = null; setUploading(false) }
  }
  const download = async (id: string, path: string) => {
    try {
      const fresh = await packageImportsApi.get(id)
      if (!fresh.source_url) throw new Error('Source download unavailable during activation or after expiry.')
      const anchor = document.createElement('a')
      anchor.href = `${fresh.source_url}&path=${encodeURIComponent(path)}`
      anchor.click()
    }
    catch (cause) { setError(String(cause)) }
  }
  const inspectFile = async (id: string, path: string) => {
    const request = ++previewRequest.current
    setPreview({ importId: id, path, loading: true })
    try {
      const blob = await packageImportsApi.source(id, path)
      const text = await blob.text()
      if (selectedRef.current === id && previewRequest.current === request) setPreview({ importId: id, path, text, loading: false })
    } catch (cause) {
      if (selectedRef.current === id && previewRequest.current === request) {
        setPreview({ importId: id, path, error: `Could not load this source file. ${String(cause)}`, loading: false })
      }
    }
  }
  const closePreview = () => {
    previewRequest.current += 1
    setPreview(null)
  }
  const select = (item: PackageImport) => {
    setSelected(item.id); setOffset(0); setError(null); closePreview()
  }

  return <Card>
    <CardHeader><CardTitle>{experimentId ? 'Imported source evidence' : 'Import experiment package'}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      {!experimentId && <>
        <p className="text-sm text-muted-foreground">Upload a published experiment package. Review its contents before creating a new editable experiment. Import never runs packaged scripts or tools.</p>
        <FileDropInput id={fileInputId} accept=".tar.gz,.tgz,.gz,application/gzip,application/x-gzip" ariaLabel="Experiment package"
          file={file} disabled={uploading} placeholder="Drop a .tar.gz or .tgz package or click to browse"
          onChange={(picked) => {
            if (picked) { setFile(picked); void upload(picked) }
          }} />
        {current?.status === 'uploading' && <p className="text-sm">To retry an interrupted upload, choose the package file again.</p>}
      </>}
      <div className="flex flex-wrap gap-2">{list.data?.map((item) => <Button key={item.id} variant={selected === item.id ? 'secondary' : 'outline'} onClick={() => select(item)}>
        <span className="font-mono">{item.id.slice(0, 8)}</span> · {statusLabels[item.status] ?? item.status}
      </Button>)}</div>
      {(error || job.error || list.error) && <p role="alert" className="text-destructive">{error ?? String(job.error ?? list.error)}</p>}
      {current && <div className="space-y-3">
        <p role="status" className="text-sm font-medium">{statusLabels[current.status] ?? current.status}{current.progress.scanned !== undefined ? ` · ${current.progress.scanned.toLocaleString()} processed` : ''}</p>
        {current.error && <p role="alert" className="text-destructive">{current.error}</p>}
        {current.preflight.manifest && <>
          <PackageSummary current={current} />
          {current.preflight.warnings?.map((warning) => <p key={warning} className="text-sm text-amber-400">{warning}</p>)}
          {current.preflight.compatibility?.map((issue) => <p key={issue} className="text-sm text-amber-400">{issue}</p>)}
          {current.preflight.diagnostics?.map((item, index) => <p key={index} className="text-sm">{item.kind} <span className="font-mono">{item.id}</span>: {item.reason ?? item.status}</p>)}
          <details className="group/source-files overflow-hidden rounded-lg border">
            <summary className="flex cursor-pointer list-none items-center justify-between gap-3 bg-muted/30 px-4 py-3 text-sm font-medium [&::-webkit-details-marker]:hidden">
              Inspect source files and historical evidence
              <ChevronDown aria-hidden="true" className="size-4 shrink-0 text-muted-foreground transition-transform group-open/source-files:rotate-180" />
            </summary>
            <p className="border-t px-4 py-3 text-xs text-muted-foreground">Browse original package files. Inspect JSON in a read-only viewer or download any file. Hover over a filename to see its SHA-256.</p>
            <div className="max-h-96 overflow-auto border-y">
              <table className="w-full min-w-[36rem] text-left text-xs">
                <caption className="sr-only">Source files and historical evidence</caption>
                <thead className="sticky top-0 z-10 bg-muted text-muted-foreground"><tr>
                  <th scope="col" className="px-4 py-2.5 font-medium">File</th>
                  <th scope="col" className="px-3 py-2.5 text-right font-medium">Size</th>
                  <th scope="col" className="px-3 py-2.5 font-medium">Source</th>
                  <th scope="col" className="px-4 py-2.5 text-right font-medium">Actions</th>
                </tr></thead>
                <tbody className="divide-y">
                  {files.isPending && <tr><td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">Loading source files…</td></tr>}
                  {files.isError && <tr><td colSpan={4} className="px-4 py-4"><p role="alert" className="text-destructive">Could not load source files. {String(files.error)}</p><Button size="xs" variant="outline" className="mt-2" onClick={() => void files.refetch()}>Retry file list</Button></td></tr>}
                  {files.data?.items.length === 0 && <tr><td colSpan={4} className="px-4 py-6 text-center text-muted-foreground">No source files listed.</td></tr>}
                  {files.data?.items.map((file) => {
                    const isJson = file.path.endsWith('.json')
                    const FileIcon = isJson ? FileJson : File
                    const separator = file.path.lastIndexOf('/')
                    return <tr key={file.path} className="transition-colors hover:bg-muted/30">
                      <th scope="row" className="px-4 py-3 font-normal"><div className="flex items-start gap-2.5">
                        <FileIcon aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                        <div className="min-w-0" title={`SHA-256: ${file.sha256}`}>
                          <p className="break-all font-mono text-foreground">{file.path.slice(separator + 1)}</p>
                          {separator >= 0 && <p className="mt-1 break-all font-mono text-[11px] text-muted-foreground">{file.path.slice(0, separator + 1)}</p>}
                        </div>
                      </div></th>
                      <td className="whitespace-nowrap px-3 py-3 text-right font-mono tabular-nums text-muted-foreground" title={`${file.bytes.toLocaleString()} bytes`}>{fileSize(file.bytes)}</td>
                      <td className="px-3 py-3"><Badge variant="outline" className="h-auto whitespace-normal text-[11px]">{provenanceLabels[file.provenance] ?? file.provenance.replaceAll('_', ' ')}</Badge></td>
                      <td className="px-4 py-3"><div className="flex justify-end gap-1.5">
                        {isJson && file.bytes <= 16 * 1024 * 1024 && <Button size="xs" variant="outline" onClick={() => void inspectFile(current.id, file.path)}><Search aria-hidden="true" className="size-3" />Inspect JSON</Button>}
                        <Button size="xs" variant="ghost" onClick={() => void download(current.id, file.path)}><Download aria-hidden="true" className="size-3" />Download</Button>
                      </div></td>
                    </tr>
                  })}
                </tbody>
              </table>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 bg-muted/20 px-4 py-2">
              <p className="text-xs text-muted-foreground">{files.data?.items.length ? `Showing files ${(offset + 1).toLocaleString()}–${(offset + files.data.items.length).toLocaleString()}` : 'Source file inventory'}</p>
              <div className="flex gap-1"><Button size="xs" variant="ghost" disabled={offset === 0 || files.isFetching} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
                <Button size="xs" variant="ghost" disabled={files.data?.next_offset == null || files.isFetching} onClick={() => setOffset(files.data!.next_offset!)}>Next</Button></div>
            </div>
          </details>
        </>}
        <div className="flex flex-wrap gap-2">
          {current.status === 'awaiting_confirmation' && <Button disabled={action.isPending} onClick={() => action.mutate({ id: current.id, command: 'confirm' })}>Confirm import into a new experiment</Button>}
          {current.status === 'failed' && <Button disabled={action.isPending} onClick={() => action.mutate({ id: current.id, command: 'retry' })}>Retry inspection</Button>}
          {!['completed', 'expired', 'cancelled'].includes(current.status) && <Button variant="outline" onClick={() => {
            uploadController.current?.abort()
            action.mutate({ id: current.id, command: 'cancel' })
          }}>Cancel import</Button>}
          {current.experiment_id && <Button render={<Link to={`/experiments/${current.experiment_id}/protocol`} />}>Open editable draft</Button>}
        </div>
        {current.status === 'completed' && current.experiment_id && !experimentId && <ImportedExperimentSetup importId={current.id} experimentId={current.experiment_id} />}
      </div>}
    </CardContent>
    <Dialog open={preview !== null && preview.importId === selected} onOpenChange={open => { if (!open) closePreview() }}>
      <DialogContent className="flex max-h-[85svh] flex-col sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>Inspect JSON</DialogTitle>
          <DialogDescription className="break-all font-mono text-xs">{preview?.path}</DialogDescription>
        </DialogHeader>
        <p className="text-xs text-muted-foreground">Read-only source file from the uploaded package.</p>
        {preview?.loading && <p role="status" className="text-sm">Loading JSON…</p>}
        {preview?.error && <div className="space-y-2">
          <p role="alert" className="text-sm text-destructive">{preview.error}</p>
          <Button variant="outline" onClick={() => void inspectFile(preview.importId, preview.path)}>Retry loading file</Button>
        </div>}
        {preview?.text !== undefined && <JsonSourceViewer key={`${preview.importId}:${preview.path}`} source={preview.text} />}
        <Button className="self-end" variant="outline" onClick={closePreview}>Close source inspection</Button>
      </DialogContent>
    </Dialog>
  </Card>
}
