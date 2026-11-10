import { useEffect, useRef, useState, type MouseEvent } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Download } from 'lucide-react'
import { experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { jobActive, useResultJob } from '@/lib/resultJobs'
import { newRequestKey } from '@/lib/requestKey'
import { protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import { executionKeys } from '@/lib/executionQueries'
import type { Protocol } from '@/types/protocols'
import { ExperimentPackageImport } from './ExperimentPackageImport'

export function ExperimentPackageDownload({ experimentId, protocol, selectedVersionId }: {
  experimentId: string; protocol: Protocol; selectedVersionId?: string
}) {
  const client = useQueryClient()
  const [open, setOpen] = useState(false)
  const [versionId, setVersionId] = useState('')
  const [includeResults, setIncludeResults] = useState(false)
  const [jobId, setJobId] = useState<string>()
  const [linkPending, setLinkPending] = useState(false)
  const [linkError, setLinkError] = useState<string>()
  const linkScope = useRef('')
  linkScope.current = JSON.stringify({ experimentId, protocolId: protocol.id, versionId, includeResults, jobId, open })
  const versions = useQuery({ queryKey: ['protocols', protocol.id, 'revisions'],
    queryFn: () => protocolsApi.listRevisions(protocol.id), enabled: open })
  const job = useResultJob(experimentId, jobId)
  useEffect(() => {
    if (open && !versionId && versions.data?.length) setVersionId(versions.data[0].id)
  }, [open, versionId, versions.data])
  const create = useMutation({
    mutationFn: (revisionId: string) => experimentsApi.createResultJob(experimentId,
      { protocol_id: protocol.id, protocol_revision_id: revisionId },
      { kind: 'export', request_key: newRequestKey(), format: 'experiment_package', include_results: includeResults }),
    onSuccess: result => setJobId(result.id),
  })
  const publish = useMutation({
    mutationFn: async () => {
      const live = client.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocol.id))
      if (live) await protocolsApi.update(protocol.id, { graph: toPersistedGraph(live.nodes, live.edges) })
      const published = await protocolsApi.publish(protocol.id)
      if (!published.published_revision_id) throw new Error('Publication did not return a downloadable version.')
      return published
    },
    onSuccess: async published => {
      client.setQueryData(protocolForExperimentQueryKey(experimentId), published)
      void client.invalidateQueries({ queryKey: ['experiments', experimentId] })
      void client.invalidateQueries({ queryKey: executionKeys.root(experimentId) })
      setVersionId(published.published_revision_id!)
      setJobId(undefined)
      await client.invalidateQueries({ queryKey: ['protocols', protocol.id, 'revisions'] })
      create.mutate(published.published_revision_id!)
    },
  })
  const needsPublish = !protocol.published_revision_id || protocol.has_unpublished_changes
  const busy = publish.isPending || create.isPending || jobActive(job.data) || (!!jobId && !job.data && !job.error)
  const error = linkError ?? publish.error?.message ?? create.error?.message ?? job.error?.message ?? job.data?.error
  async function download(event: MouseEvent<HTMLAnchorElement>, metadata: boolean) {
    event.preventDefault()
    if (linkPending || !jobId) return
    const requestedJob = jobId
    const requestedScope = linkScope.current
    setLinkError(undefined)
    setLinkPending(true)
    try {
      const fresh = (await job.refetch({ throwOnError: true })).data
      if (linkScope.current !== requestedScope) return
      const url = metadata ? fresh?.schema_url : fresh?.download_url
      if (fresh?.id !== requestedJob || fresh.protocol_revision_id !== versionId || fresh.status !== 'completed' || !url) {
        setLinkError('This package is no longer available. Prepare a new download.')
        return
      }
      // Keep the server's native Content-Disposition download; only status is
      // fetched through the authenticated API, never the archive as a Blob.
      const link = document.createElement('a')
      link.href = url
      link.click()
    } catch {
      if (linkScope.current === requestedScope) setLinkError('Could not refresh the download link. Try again.')
    } finally {
      setLinkPending(false)
    }
  }
  return <>
    <Button size="xs" variant="outline" onClick={() => {
      setVersionId(selectedVersionId || protocol.published_revision_id || '')
      setJobId(undefined)
      setLinkError(undefined)
      create.reset()
      publish.reset()
      setOpen(true)
    }}><Download className="size-3.5" /> Download experiment package</Button>
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-h-[90svh] overflow-y-auto sm:max-w-lg">
        <DialogHeader><DialogTitle>Download published experiment</DialogTitle></DialogHeader>
        <ExperimentPackageImport experimentId={experimentId} />
        <label className="space-y-1 text-sm">Published version
          <select className="w-full rounded-md border bg-background px-2 py-2 font-mono text-sm" value={versionId}
            disabled={busy} onChange={event => { setVersionId(event.target.value); setJobId(undefined); setLinkError(undefined); create.reset(); publish.reset() }}>
            {!versions.data && <option value={versionId}>Loading versions…</option>}
            {versions.data?.map(version => <option key={version.id} value={version.id}>
              v{version.revision}{version.name ? ` · ${version.name}` : ''} · {new Date(version.published_at).toLocaleString()}
            </option>)}
          </select>
        </label>
        <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={includeResults} disabled={busy}
          onChange={event => { setIncludeResults(event.target.checked); setJobId(undefined); setLinkError(undefined); create.reset() }} />
          Include this version’s recorded results and retained artifacts</label>
        <p className="text-xs text-muted-foreground">Includes published configuration and pinned dataset inputs. Reusable skills and knowledge are captured at download time. The manifest identifies missing dependencies and historical evidence.</p>
        <p className="text-xs text-muted-foreground">Credentials are excluded. Prepared downloads expire after 24 hours.</p>
        {versions.isError && <p role="alert" className="text-xs text-destructive">Could not load published versions.</p>}
        {versions.data?.length === 0 && <p role="status" className="text-xs text-muted-foreground">Publish the experiment to create its first downloadable version.</p>}
        {needsPublish && <div className="space-y-1 rounded-md border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm">
          <p className="font-medium">{protocol.published_revision_id ? 'Experiment has unpublished changes' : 'Experiment has not been published'}</p>
          <p className="text-xs text-muted-foreground">Publish the draft to include its current canvas, design settings, and dataset inputs in a new package. Preparing a selected published version includes only that version’s recorded configuration and inputs.</p>
        </div>}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        {job.data?.status === 'expired' && <p role="status" className="text-xs">This package expired. Prepare another download.</p>}
        {job.data?.status === 'cancelled' && <p role="status" className="text-xs">Package preparation cancelled.</p>}
        {busy && <p role="status" className="font-mono text-xs">Preparing package · {job.data?.progress.bytes?.toLocaleString() ?? 0} bytes</p>}
        {linkPending && <p role="status" className="text-xs text-muted-foreground">Refreshing download link…</p>}
        {job.data?.status === 'completed' && job.data.progress.sha256 && <p className="break-all font-mono text-xs text-muted-foreground">SHA-256: {job.data.progress.sha256}</p>}
        <div className="flex flex-wrap gap-2">
          {needsPublish && <Button size="sm" disabled={busy} onClick={() => {
            setLinkError(undefined)
            create.reset()
            publish.mutate()
          }}>{publish.isPending ? 'Publishing…' : 'Publish & prepare package'}</Button>}
          <Button size="sm" disabled={busy || !versionId || !versions.data || versions.isError} onClick={() => {
            setLinkError(undefined)
            create.mutate(versionId)
          }}>
            {busy ? 'Preparing…' : 'Prepare package'}
          </Button>
          {busy && jobId && <Button size="sm" variant="outline" onClick={() => {
            void experimentsApi.cancelResultJob(experimentId, jobId).then(() => job.refetch())
          }}>Cancel preparation</Button>}
          {job.data?.status === 'completed' && <>
            <a className="text-sm text-primary underline" href={job.data.download_url} aria-disabled={linkPending}
              onClick={event => { void download(event, false) }}>Download prepared package</a>
            <a className="text-sm text-primary underline" href={job.data.schema_url} aria-disabled={linkPending}
              onClick={event => { void download(event, true) }}>Manifest</a>
          </>}
        </div>
      </DialogContent>
    </Dialog>
  </>
}
