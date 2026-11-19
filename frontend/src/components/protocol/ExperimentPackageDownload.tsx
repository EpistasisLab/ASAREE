import { useEffect, useRef, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { Download } from 'lucide-react'
import { experimentsApi, protocolsApi } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { jobActive, useResultJob } from '@/lib/resultJobs'
import { newRequestKey } from '@/lib/requestKey'
import { protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import type { Protocol } from '@/types/protocols'

export function ExperimentPackageDownload({ experimentId, protocol, selectedVersionId, draftBusy = false }: {
  experimentId: string; protocol: Protocol; selectedVersionId?: string; draftBusy?: boolean
}) {
  const [publicationWarning, setPublicationWarning] = useState(false)
  const client = useQueryClient()
  const [jobId, setJobId] = useState<string>()
  const [revisionId, setRevisionId] = useState<string>()
  const [requestScope, setRequestScope] = useState<string>()
  const downloaded = useRef<string | null>(null)
  const scope = JSON.stringify({ experimentId, protocolId: protocol.id, version: selectedVersionId || 'draft' })
  const job = useResultJob(experimentId, jobId)
  const prepare = useMutation({
    mutationFn: async () => {
      const fresh = await protocolsApi.get(protocol.id)
      const live = client.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocol.id))
      const saved = toPersistedGraph(fresh.graph.nodes as Node[], fresh.graph.edges as Edge[])
      const unsaved = live && JSON.stringify(toPersistedGraph(live.nodes, live.edges)) !== JSON.stringify(saved)
      if (!selectedVersionId && (draftBusy || unsaved || fresh.has_unpublished_changes || !fresh.published_revision_id)) {
        setPublicationWarning(true)
        return null
      }
      const version = selectedVersionId || fresh.published_revision_id!
      setRevisionId(version)
      setRequestScope(scope)
      return experimentsApi.createResultJob(experimentId,
        { protocol_id: protocol.id, protocol_revision_id: version },
        { kind: 'export', request_key: newRequestKey(), format: 'experiment_package' })
    },
    onSuccess: result => setJobId(result?.id),
  })
  useEffect(() => {
    const ready = job.data
    if (ready?.status !== 'completed' || !ready.download_url || requestScope !== scope
      || ready.protocol_revision_id !== revisionId || downloaded.current === ready.id) return
    downloaded.current = ready.id
    const link = document.createElement('a')
    link.href = ready.download_url
    link.click()
  }, [job.data, revisionId, scope, requestScope])
  const busy = prepare.isPending || jobActive(job.data) || (!!jobId && !job.data && !job.error)
  const error = prepare.error?.message || job.error?.message || job.data?.error
  return <>
    <Button size="xs" variant="outline" disabled={busy} onClick={() => {
      prepare.reset()
      setJobId(undefined)
      if (!selectedVersionId && (draftBusy || protocol.has_unpublished_changes || !protocol.published_revision_id)) {
        setPublicationWarning(true)
      } else prepare.mutate()
    }}><Download className="size-3.5" />{busy ? 'Preparing download…' : 'Download'}</Button>
    {busy && <span role="status" className="text-xs text-muted-foreground">Preparing experiment and input assets…</span>}
    {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
    <Dialog open={publicationWarning} onOpenChange={setPublicationWarning}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader><DialogTitle>Publish before exporting</DialogTitle></DialogHeader>
        <p className="text-sm">Publish your current canvas and design changes using Publish experiment, then download the package.</p>
        <p className="text-xs text-muted-foreground">The package includes all required input assets. Results and execution history are excluded.</p>
        <Button onClick={() => setPublicationWarning(false)}>Close</Button>
      </DialogContent>
    </Dialog>
  </>
}
