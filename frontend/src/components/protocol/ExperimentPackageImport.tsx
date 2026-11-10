import { useId, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { packageImportsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { PackageImport } from '@/types/packageImports'
import { newRequestKey } from '@/lib/requestKey'
import { FileDropInput } from './FileDropInput'

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
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [preview, setPreview] = useState<{ importId: string; path: string; text: string } | null>(null)
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
  const download = async (id: string, path?: string) => {
    try {
      const fresh = await packageImportsApi.get(id)
      if (!fresh.source_url) throw new Error('Source download unavailable during activation or after expiry.')
      const anchor = document.createElement('a')
      anchor.href = fresh.source_url + (path ? `&path=${encodeURIComponent(path)}` : '')
      anchor.click()
    }
    catch (cause) { setError(String(cause)) }
  }
  const inspectFile = async (id: string, path: string) => {
    try {
      const blob = await packageImportsApi.source(id, path)
      const text = await blob.text()
      if (selectedRef.current === id) setPreview({ importId: id, path, text })
    } catch (cause) { if (selectedRef.current === id) setError(String(cause)) }
  }
  const select = (item: PackageImport) => {
    setSelected(item.id); setOffset(0); setAcknowledged(false); setError(null); setPreview(null)
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
          <Button variant="outline" onClick={() => void download(current.id)}>Download original source package</Button>
          <details><summary className="cursor-pointer">Inspect source files and historical evidence</summary>
            <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>File</th><th>Bytes</th><th>Provenance</th><th /></tr></thead>
              <tbody>{files.data?.items.map((file) => <tr key={file.path}><td className="break-all font-mono" title={file.sha256}>{file.path}</td><td>{file.bytes}</td><td>{file.provenance}</td><td>
                {file.path.endsWith('.json') && file.bytes <= 16 * 1024 * 1024 && <Button variant="ghost" onClick={() => void inspectFile(current.id, file.path)}>Inspect JSON</Button>}
                <Button variant="ghost" onClick={() => void download(current.id, file.path)}>Download</Button>
              </td></tr>)}</tbody></table></div>
            <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
            <Button variant="ghost" disabled={files.data?.next_offset == null} onClick={() => setOffset(files.data!.next_offset!)}>Next</Button>
          </details>
          {preview?.importId === current.id && <div className="space-y-2">
            <p className="break-all font-mono text-xs">Immutable source evidence: {preview.path}</p>
            <pre className="max-h-80 overflow-auto rounded border p-3 text-xs">{preview.text}</pre>
            <Button variant="ghost" onClick={() => setPreview(null)}>Close source inspection</Button>
          </div>}
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
        {current.status === 'completed' && !current.configured_at && <div className="space-y-2">
          <p className="text-sm">Configure connections and resolve missing dependencies in the draft. Historical source facts stay unchanged. Then acknowledge configuration and use normal publication before production execution.</p>
          <label className="flex gap-2 text-sm"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />I configured local connections and resolved required dependencies.</label>
          <Button disabled={!acknowledged} onClick={() => void packageImportsApi.configure(current.id).then(() => job.refetch()).catch((cause: unknown) => setError(String(cause)))}>Acknowledge local configuration</Button>
        </div>}
      </div>}
    </CardContent>
  </Card>
}
