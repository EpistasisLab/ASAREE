import { useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { packageImportsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import type { PackageImport } from '@/types/packageImports'

/** Persistent imports can be resumed after navigation or a browser restart. */
export function ExperimentPackageImport({ experimentId }: { experimentId?: string }) {
  const client = useQueryClient()
  const [selected, setSelected] = useState<string | null>(null)
  const [offset, setOffset] = useState(0)
  const [acknowledged, setAcknowledged] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const uploadController = useRef<AbortController | null>(null)
  const list = useQuery({ queryKey: ['package-imports', experimentId], queryFn: () => packageImportsApi.list(experimentId),
    refetchInterval: 3000 })
  const job = useQuery({ queryKey: ['package-import', selected], enabled: selected !== null,
    queryFn: () => packageImportsApi.get(selected!), refetchInterval: 1000 })
  const current = job.data
  const files = useQuery({ queryKey: ['package-import-files', selected, offset],
    enabled: !!current?.preflight.manifest && !['expired', 'cancelled'].includes(current.status),
    queryFn: () => packageImportsApi.files(selected!, offset) })
  const action = useMutation({
    mutationFn: async ({ id, command }: { id: string; command: 'confirm' | 'cancel' | 'retry' }) => packageImportsApi.command(id, command),
    onSuccess: () => { void client.invalidateQueries({ queryKey: ['package-import'] }); void client.invalidateQueries({ queryKey: ['experiments'] }) },
    onError: (cause) => setError(String(cause)),
  })
  const upload = async (file: File) => {
    setError(null)
    try {
      const created = await packageImportsApi.create(crypto.randomUUID())
      setSelected(created.id)
      setOffset(0)
      const controller = new AbortController()
      uploadController.current = controller
      await packageImportsApi.upload(created.id, file, controller.signal)
      void client.invalidateQueries({ queryKey: ['package-imports'] })
    } catch (cause) { setError(String(cause)) }
    finally { uploadController.current = null }
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
  const select = (item: PackageImport) => { setSelected(item.id); setOffset(0); setAcknowledged(false); setError(null) }

  return <Card>
    <CardHeader><CardTitle>{experimentId ? 'Imported source evidence' : 'Import experiment package'}</CardTitle></CardHeader>
    <CardContent className="space-y-4">
      {!experimentId && <>
        <p className="text-sm text-muted-foreground">Upload a published experiment package. Review its contents before creating a new editable experiment. Import never runs packaged scripts or tools.</p>
        <Input type="file" accept=".tar.gz,.tgz" aria-label="Experiment package" onChange={(event) => {
          const file = event.target.files?.[0]
          if (file) void upload(file)
          event.target.value = ''
        }} />
      </>}
      <div className="flex flex-wrap gap-2">{list.data?.map((item) => <Button key={item.id} variant={selected === item.id ? 'secondary' : 'outline'} onClick={() => select(item)}>
        <span className="font-mono">{item.id.slice(0, 8)}</span> · {item.status}
      </Button>)}</div>
      {(error || job.error || list.error) && <p role="alert" className="text-destructive">{error ?? String(job.error ?? list.error)}</p>}
      {current && <div className="space-y-3">
        <p role="status">{current.status} · {current.progress.phase}</p>
        {current.error && <p role="alert" className="text-destructive">{current.error}</p>}
        {current.preflight.manifest && <>
          <p>Source version {current.preflight.manifest.publication.version} · schema {current.preflight.manifest.schema_version} · {current.preflight.manifest.results_included ? 'Historical results included' : 'No historical results'}</p>
          <p className="break-all font-mono text-xs">Source publication: {current.preflight.manifest.publication.id}<br />Archive SHA-256: {current.sha256}</p>
          <p className="text-sm">{Object.entries(current.preflight.categories ?? {}).map(([name, count]) => `${name}: ${count}`).join(' · ')}</p>
          <p>{current.preflight.unresolved_count} unresolved dependencies · {current.preflight.external_requirements} external requirements</p>
          {current.preflight.warnings?.map((warning) => <p key={warning} className="text-sm text-amber-400">{warning}</p>)}
          {current.preflight.compatibility?.map((issue) => <p key={issue} className="text-sm text-amber-400">{issue}</p>)}
          {current.preflight.diagnostics?.map((item, index) => <p key={index} className="text-sm">{item.kind} <span className="font-mono">{item.id}</span>: {item.reason ?? item.status}</p>)}
          <Button variant="outline" onClick={() => void download(current.id)}>Download original source package</Button>
          <details><summary className="cursor-pointer">Inspect source files and historical evidence</summary>
            <div className="overflow-x-auto"><table className="w-full text-left text-xs"><thead><tr><th>File</th><th>Bytes</th><th>Provenance</th><th /></tr></thead>
              <tbody>{files.data?.items.map((file) => <tr key={file.path}><td className="break-all font-mono" title={file.sha256}>{file.path}</td><td>{file.bytes}</td><td>{file.provenance}</td><td><Button variant="ghost" onClick={() => void download(current.id, file.path)}>Download</Button></td></tr>)}</tbody></table></div>
            <Button variant="ghost" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 50))}>Previous</Button>
            <Button variant="ghost" disabled={files.data?.next_offset == null} onClick={() => setOffset(files.data!.next_offset!)}>Next</Button>
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
        {current.status === 'completed' && !current.configured_at && <div className="space-y-2">
          <p className="text-sm">Configure connections and resolve missing dependencies in the draft. Historical source facts stay unchanged. Then acknowledge configuration and use normal publication before production execution.</p>
          <label className="flex gap-2 text-sm"><input type="checkbox" checked={acknowledged} onChange={(event) => setAcknowledged(event.target.checked)} />I configured local connections and resolved required dependencies.</label>
          <Button disabled={!acknowledged} onClick={() => void packageImportsApi.configure(current.id).then(() => job.refetch()).catch((cause: unknown) => setError(String(cause)))}>Acknowledge local configuration</Button>
        </div>}
      </div>}
    </CardContent>
  </Card>
}
