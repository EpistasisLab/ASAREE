import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { mcpServersApi } from '@/api/client'
import { connectedTools, toolId, TOOL_FACTOR_PATH } from '@/lib/toolFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useToolTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const library = useQuery({ queryKey: ['mcp-servers'], queryFn: () => mcpServersApi.list(), enabled: !!graph?.nodes.some((node) => node.data.factor_bindings?.[TOOL_FACTOR_PATH]) })
  const owners = graph?.nodes.filter((node) => node.data.factor_bindings?.[TOOL_FACTOR_PATH] && (!nodeId || node.id === nodeId)) ?? []
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.tool_factor_mode, node.data.tool_selection, connectedTools(graph!, node.id).map(toolId)]))
  useEffect(() => { setChoices({}) }, [signature])
  const selections = Object.fromEntries(owners.map((node) => {
    if (node.data.tool_factor_mode === 'tool_toggle') return [node.id, choices[node.id] ?? ((node.data.tool_selection as string[] | undefined)?.length ? 'all' : 'none')]
    const ids = connectedTools(graph!, node.id).map(toolId)
    const baseline = (node.data.tool_selection as string[] | undefined)?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const error = owners.length === 0 ? null : library.isError ? 'Could not load available tools.' : !library.data ? 'Loading tools…' : owners.some((node) => {
    const ids = node.data.tool_factor_mode === 'tool_toggle' ? (selections[node.id] === 'all' ? connectedTools(graph!, node.id).map(toolId) : []) : [selections[node.id]]
    return ids.some((id) => {
      const config = graph!.nodes.find((node) => node.id === id)?.data.config as { server_id?: string; tool_names?: string[] } | undefined
      const server = library.data.find((server) => server.id === config?.server_id)
      return !server || !config?.tool_names?.length || config.tool_names.some((name) => !server.capabilities?.tools?.some((tool) => tool.name === name))
    })
  }) ? 'Select an available tool for each agent.' : null
  return { graph, owners, library, selections, options: owners.length ? { tool_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
