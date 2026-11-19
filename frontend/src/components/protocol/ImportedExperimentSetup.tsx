import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { packageImportsApi, protocolsApi } from '@/api/client'
import { protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import type { Edge, Node } from '@xyflow/react'
import type { Protocol, ProtocolGraph } from '@/types/protocols'
import { Button } from '@/components/ui/button'

export function ImportedExperimentSetup({ importId, experimentId, onSelectNode, protocolId, onGraphChange }: {
  importId: string; experimentId: string; onSelectNode?: (id: string) => void
  protocolId?: string; onGraphChange?: (graph: ProtocolGraph) => void
}) {
  const client = useQueryClient()
  const effectiveProtocolId = protocolId ?? client.getQueryData<Protocol>(protocolForExperimentQueryKey(experimentId))?.id
  const setup = useQuery({ queryKey: ['import-readiness', importId], queryFn: () => packageImportsApi.readiness(importId) })
  const selection = useMutation({
    mutationFn: async ({ kind, source, destination }: { kind: 'mcp' | 'provider'; source: string; destination: string }) => {
      if (effectiveProtocolId) {
        const live = client.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(effectiveProtocolId))
        if (live) await protocolsApi.update(effectiveProtocolId, { graph: toPersistedGraph(live.nodes, live.edges) })
      }
      return packageImportsApi.selectConnection(importId, kind, source, destination)
    },
    onSuccess: async data => {
      client.setQueryData(['import-readiness', importId], data)
      if (effectiveProtocolId) {
        const fresh = await protocolsApi.get(effectiveProtocolId)
        client.setQueryData(protocolForExperimentQueryKey(experimentId), fresh)
        onGraphChange?.(fresh.graph)
      }
      void client.invalidateQueries({ queryKey: ['experiments', experimentId] })
    },
  })
  return <section aria-label="Imported experiment setup" className="space-y-2 rounded-md border p-3 text-sm">
    <p className="font-medium">{setup.data ? setup.data.ready ? 'Ready to publish' : 'Setup needed' : 'Checking publication readiness…'}</p>
    <p className="text-xs text-muted-foreground">Checks saved configuration only. Test live connections explicitly in LLM Settings or MCP Servers. Publish normally when setup is complete.</p>
    <Link to="/profile" className="text-xs underline">Manage destination model credentials</Link>
    {(setup.error || selection.error) && <p role="alert">{String(setup.error || selection.error)}</p>}
    {setup.data?.issues.map((issue, index) => <p key={index} className="text-amber-400">
      {issue.node_id && onSelectNode ? <button className="underline" onClick={() => onSelectNode(issue.node_id!)}>{issue.message}</button>
        : <Link className="underline" to={`/experiments/${experimentId}/protocol?node=${encodeURIComponent(issue.node_id ?? '')}`}>{issue.message}</Link>}
    </p>)}
    {setup.data?.providers.map(provider => <div key={provider.id}>Model provider: {provider.provider}
      <Button className="ml-2" size="xs" variant="outline" disabled={provider.selected || selection.isPending}
        onClick={() => selection.mutate({ kind: 'provider', source: provider.provider, destination: provider.id })}>
        {provider.selected ? 'Destination connection selected' : 'Use my saved connection'}</Button>
    </div>)}
    {setup.data?.mcp.map(requirement => <label key={requirement.source_id} className="block">{requirement.name}
      <select aria-label={`Destination connection for ${requirement.name}`} className="ml-2 rounded border bg-background p-1"
        disabled={selection.isPending} value={requirement.selected_id ?? ''} onChange={event => {
          if (event.target.value) selection.mutate({ kind: 'mcp', source: requirement.source_id, destination: event.target.value })
        }}>
        <option value="">{requirement.candidates.length ? 'Select destination connection' : 'No compatible destination connection'}</option>
        {requirement.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name} · {candidate.id.slice(0, 8)}</option>)}
      </select>
      {!!requirement.required_tools?.length && <span className="block text-xs text-muted-foreground">Required tools: {requirement.required_tools.join(', ')}</span>}
      {requirement.candidates.length > 1 && !requirement.selected_id && <span className="block text-xs text-amber-400">Multiple compatible connections. Select the destination explicitly.</span>}
    </label>)}
    <Button size="xs" variant="outline" disabled={setup.isFetching} onClick={() => void setup.refetch()}>Refresh readiness</Button>
  </section>
}
