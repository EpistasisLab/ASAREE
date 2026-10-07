import { useEffect, useState } from 'react'
import { connectedSubAgents, SUB_AGENT_FACTOR_PATH } from '@/lib/subAgentFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useSubAgentTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const owners = graph?.nodes.filter((node) => node.data.factor_bindings?.[SUB_AGENT_FACTOR_PATH] && (!nodeId || node.id === nodeId)) ?? []
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.sub_agent_factor_mode, node.data.sub_agent_selection, connectedSubAgents(graph!, node.id).map((child) => child.id)]))
  useEffect(() => { setChoices({}) }, [signature])
  const selections = Object.fromEntries(owners.map((node) => {
    const selected = node.data.sub_agent_selection as string[] | undefined
    if (node.data.sub_agent_factor_mode === 'sub_agent_toggle') return [node.id, choices[node.id] ?? (selected?.length ? 'all' : 'none')]
    const ids = connectedSubAgents(graph!, node.id).map((child) => child.id)
    const baseline = selected?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const error = owners.some((node) => {
    const children = connectedSubAgents(graph!, node.id)
    return children.some((child) => child.data.factor_bindings?.active) ||
      (node.data.sub_agent_factor_mode === 'sub_agent_toggle'
        ? selections[node.id] === 'all' && children.length === 0
        : !children.some((child) => child.id === selections[node.id]))
  }) ? 'Select a connected Sub-Agent for each agent and remove conflicting individual on/off bindings.' : null
  return { graph, owners, selections, options: owners.length ? { sub_agent_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
