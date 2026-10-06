import { useQuery } from '@tanstack/react-query'
import { datasetsApi, experimentsApi, protocolsApi } from '@/api/client'
import { Card } from '@/components/ui/card'
import { datasetRowTopologyError, rowBindingForNode } from '@/lib/datasetRows'
import type { Protocol, ProtocolGraph } from '@/types/protocols'

export function DatasetExecutionSummary({ graph, protocol, experimentId, cellCount, replicateCount }: {
  graph?: ProtocolGraph
  protocol?: Protocol
  experimentId: string
  cellCount: number
  replicateCount: number
}) {
  const binding = graph && rowBindingForNode(graph)
  const datasetId = binding?.config?.dataset_id
  const source = useQuery({
    queryKey: ['datasets', datasetId, 'row-schema'],
    queryFn: () => datasetsApi.getRowSchema(datasetId!),
    enabled: !!datasetId,
    retry: false,
  })
  const publication = useQuery({
    queryKey: ['protocols', protocol?.id, 'row-published-graph', protocol?.published_revision_id],
    queryFn: () => protocolsApi.getRevision(protocol!.id, protocol!.published_revision_id!),
    enabled: !!protocol?.published_revision_id,
  })
  const results = useQuery({
    queryKey: ['experiments', experimentId, 'run-results', protocol?.id, protocol?.published_revision_id],
    queryFn: () => experimentsApi.getRunResults(experimentId, { protocol_id: protocol!.id }),
    enabled: !!protocol?.id && !!protocol.published_revision_id,
    refetchInterval: 5000,
  })
  const error = graph && datasetRowTopologyError(graph)
  const publishedBinding = publication.data && rowBindingForNode(publication.data.graph)
  const summary = results.data?.row_summary
  const rows = source.data?.row_count
  const columnsMissing = binding?.input.mode === 'per_row' && source.data && binding.input.columns.some(column => !source.data.columns.includes(column))

  return <Card className="gap-3 p-3" aria-label="Dataset execution">
    <p className="text-sm font-medium">Dataset execution</p>
    <div className="space-y-1 text-xs">
      <p className="font-medium">Experiment draft · proposed design</p>
      {!graph ? <p className="text-muted-foreground">Loading canvas inputs…</p> : binding ? <>
        <p>Per row · {binding.config?.dataset_name ?? datasetId ?? 'Unregistered Dataset'} · {rows === undefined ? 'row count unavailable' : `${rows} original rows`}</p>
        <p className="break-words font-mono">{cellCount} cells × {replicateCount} replicates per cell × {rows ?? '?'} rows = {rows === undefined ? '?' : cellCount * replicateCount * rows} executions</p>
        <p className="text-muted-foreground">Each row runs the complete protocol. Agents receive their selected columns.</p>
        {(error || source.error || columnsMissing || rows === 0) && <p role="alert" className="text-destructive">{error || source.error?.message || (columnsMissing ? 'Selected columns are absent from the original CSV.' : 'The original Dataset has no rows.')}</p>}
      </> : <p>Whole dataset · one protocol execution per replicate.</p>}
    </div>
    <div className="space-y-1 border-t pt-2 text-xs">
      <p className="font-medium">Production runs · {protocol?.published_revision_id ? `experiment version ${protocol.published_revision}` : 'not published'}</p>
      {!protocol?.published_revision_id ? <p className="text-muted-foreground">Publish the experiment before running.</p> : publication.isError || results.isError ? <p role="alert" className="text-destructive">Published execution forecast unavailable.</p> : !publication.data || !results.data ? <p className="text-muted-foreground">Loading published inputs…</p> : results.data.consumption_mode === 'per_row' ? <>
        <p>Per row · {publishedBinding?.config?.dataset_name ?? publishedBinding?.config?.dataset_id ?? 'Dataset'} · {summary?.row_count ?? '?'} original rows</p>
        {summary?.parent_replicate_count ? <p className="break-words font-mono">{summary.cell_count} cells / {summary.parent_replicate_count} generated replicates × {summary.row_count ?? '?'} rows = {summary.expected ?? '?'} executions</p> : <p className="text-muted-foreground">No generated replicates. Generate cells before running a batch.</p>}
        <p className="text-muted-foreground">Uses the design saved with this experiment version. Review the forecast in Runs before launching.</p>
      </> : <p>Whole dataset · one protocol execution per replicate.</p>}
      <p className="text-muted-foreground">Draft edits take effect after publishing; design changes require generation before running.</p>
    </div>
  </Card>
}
