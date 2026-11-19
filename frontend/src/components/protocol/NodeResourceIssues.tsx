import { useContext, type ReactNode } from 'react'
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import type { Edge, Node } from '@xyflow/react'
import { datasetsApi, skillsApi, mcpServersApi, okfApi, llmSettingsApi, packageImportsApi, protocolsApi } from '@/api/client'
import { protocolForExperimentQueryKey, protocolGraphQueryKey, toPersistedGraph } from '@/lib/protocolGraph'
import type { ProtocolGraph } from '@/types/protocols'
import type { ImportReadiness } from '@/types/packageImports'
import { nodeResourceIssues } from './nodeConfigIssues'
import { ResourceIssues } from './nodeResourceIssuesContext'

export function NodeResourceIssuesProvider({ experimentId, protocolId, selectedNodeId, onGraphChange, nodes, children }: {
  experimentId?: string | null; protocolId: string; selectedNodeId: string | null
  onGraphChange: (graph: ProtocolGraph) => void; children: ReactNode
  nodes: Node[]
}) {
  const client = useQueryClient()
  const kinds = new Set(nodes.map(node => node.type))
  const registries = useQueries({ queries: [
    { queryKey: ['datasets'], queryFn: datasetsApi.list, enabled: kinds.has('dataset') },
    { queryKey: ['skills'], queryFn: skillsApi.list, enabled: kinds.has('skill') },
    { queryKey: ['mcp-servers'], queryFn: mcpServersApi.list, enabled: ['mcp_tool', 'mcp_client_tool', 'mcp_scikit_learn'].some(kind => kinds.has(kind)) },
    { queryKey: ['okf-bundles'], queryFn: okfApi.list, enabled: kinds.has('okf_bundle') },
    { queryKey: ['okf-documents'], queryFn: okfApi.listDocuments, enabled: kinds.has('okf_document') },
    { queryKey: ['llm-settings'], queryFn: llmSettingsApi.list, enabled: [...kinds].some(kind => kind?.startsWith('model_') || kind?.startsWith('llm_')) },
  ] })
  // Observe data updates as well as the initial load, including inspector repairs.
  const loadedResources = registries.map(registry => registry.data)
  const sources = useQuery({ queryKey: ['package-imports', experimentId], enabled: !!experimentId,
    queryFn: () => packageImportsApi.list(experimentId!) })
  const source = sources.data?.find(item => item.status === 'completed')
  const readiness = useQuery({ queryKey: ['import-readiness', source?.id], enabled: !!source,
    queryFn: () => packageImportsApi.readiness(source!.id), refetchInterval: source ? 5000 : false })
  const selection = useMutation({
    mutationFn: async ({ requirement, destination }: { requirement: string; destination: string }) => {
      const live = client.getQueryData<{ nodes: Node[]; edges: Edge[] }>(protocolGraphQueryKey(protocolId))
      if (live) await protocolsApi.update(protocolId, { graph: toPersistedGraph(live.nodes, live.edges) })
      return packageImportsApi.selectConnection(source!.id, 'mcp', requirement, destination)
    },
    onSuccess: async result => {
      client.setQueryData(['import-readiness', source!.id], result)
      const fresh = await protocolsApi.get(protocolId)
      client.setQueryData(protocolForExperimentQueryKey(experimentId!), fresh)
      onGraphChange(fresh.graph)
      void client.invalidateQueries({ queryKey: ['experiments', experimentId] })
    },
  })
  const localIssues = loadedResources.some(Boolean) ? nodes.flatMap(node => nodeResourceIssues(node, client)
    .map(message => ({ node_id: node.id, message }))) : []
  const issues: ImportReadiness['issues'] = [...(readiness.data?.issues ?? []), ...localIssues]
  const selectedIssues = issues.filter(issue => issue.node_id === selectedNodeId)
  const requirements = readiness.data?.mcp.filter(requirement => selectedIssues.some(issue => issue.resource_id === requirement.source_id)) ?? []
  const inspector = <>
    {selectedIssues.map((issue, index) => <p key={index} className="text-sm text-amber-400">{issue.message}</p>)}
    {requirements.map(requirement => <label key={requirement.source_id} className="block space-y-1 text-sm">
      Destination connection for {requirement.name}
      <select className="block w-full rounded border bg-background p-2" value={requirement.selected_id ?? ''}
        disabled={selection.isPending} onChange={event => {
          if (event.target.value) selection.mutate({ requirement: requirement.source_id, destination: event.target.value })
        }}>
        <option value="">Choose a compatible saved connection</option>
        {requirement.candidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name}</option>)}
      </select>
      {!requirement.candidates.length && <span className="text-xs text-muted-foreground">Add the required connection in MCP Servers, then return to this node.</span>}
    </label>)}
    {(readiness.error || selection.error) && <p role="alert">{(readiness.error || selection.error)?.message}</p>}
  </>
  return <ResourceIssues.Provider value={{ issues, inspector }}>{children}</ResourceIssues.Provider>
}

export function NodeInspectorResourceIssues() {
  return useContext(ResourceIssues).inspector
}
