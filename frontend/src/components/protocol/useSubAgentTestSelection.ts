import { sharedFactorOwners, sharedGroupIssues } from '@/lib/sharedFactors'
import { useEffect, useState } from 'react'
import { connectedSubAgents, SUB_AGENT_FACTOR_PATH } from '@/lib/subAgentFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useSubAgentTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const owners = sharedFactorOwners(graph, SUB_AGENT_FACTOR_PATH, nodeId)
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.sub_agent_factor_mode, node.data.sub_agent_selection, connectedSubAgents(graph!, node.id).map((child) => child.id)]))
  useEffect(() => { setChoices({}) }, [signature, nodeId])
  const selections = Object.fromEntries(owners.map((node) => {
    const selected = node.data.sub_agent_selection as string[] | undefined
    if (node.data.sub_agent_factor_mode === 'sub_agent_toggle') return [node.id, choices[node.id] ?? (selected?.length ? 'all' : 'none')]
    const ids = connectedSubAgents(graph!, node.id).map((child) => child.id)
    const baseline = selected?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const selectionError = owners.some((node) => {
    const children = connectedSubAgents(graph!, node.id)
    return children.some((child) => child.data.factor_bindings?.active) ||
      (node.data.sub_agent_factor_mode === 'sub_agent_toggle'
        ? selections[node.id] === 'all' && children.length === 0
        : !children.some((child) => child.id === selections[node.id]))
  }) ? 'Select a connected Sub-Agent for each shared group and remove conflicting individual on/off bindings.' : null
  const factors = owners.map((node) => ({ name: node.data.factor_bindings![SUB_AGENT_FACTOR_PATH], level_type: (node.data.sub_agent_factor_mode ?? 'sub_agent_selection') as 'sub_agent_selection' | 'sub_agent_toggle', levels: [] }))
  const error = sharedGroupIssues(graph, factors, 'sub_agent')[0] ?? selectionError
  return { graph, owners, selections, options: owners.length ? { sub_agent_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
