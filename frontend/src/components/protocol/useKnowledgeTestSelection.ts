import { useEffect, useState } from 'react'
import { useKnowledgeLibrary } from './useKnowledgeLibrary'
import { connectedKnowledge, knowledgeId, KNOWLEDGE_FACTOR_PATH } from '@/lib/knowledgeFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useKnowledgeTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const library = useKnowledgeLibrary(!!graph?.nodes.some((node) => node.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH]))
  const owners = graph?.nodes.filter((node) => node.data.factor_bindings?.[KNOWLEDGE_FACTOR_PATH] && (!nodeId || node.id === nodeId)) ?? []
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.knowledge_factor_mode, node.data.knowledge_selection, connectedKnowledge(graph!, node.id).map(knowledgeId)]))
  useEffect(() => { setChoices({}) }, [signature])
  const selections = Object.fromEntries(owners.map((node) => {
    if (node.data.knowledge_factor_mode === 'knowledge_toggle') return [node.id, choices[node.id] ?? ((node.data.knowledge_selection as string[] | undefined)?.length ? 'all' : 'none')]
    const ids = connectedKnowledge(graph!, node.id).map(knowledgeId)
    const baseline = (node.data.knowledge_selection as string[] | undefined)?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const error = owners.length === 0 ? null : library.isError ? 'Could not load available knowledge.' : !library.data ? 'Loading knowledge…' : owners.some((node) => {
    const ids = node.data.knowledge_factor_mode === 'knowledge_toggle' ? (selections[node.id] === 'all' ? connectedKnowledge(graph!, node.id).map(knowledgeId) : []) : [selections[node.id]]
    return ids.some((id) => !library.data.some((knowledge) => knowledge.id === id))
  }) ? 'Select an available knowledge source for each agent.' : null
  return { graph, owners, library, selections, options: owners.length ? { knowledge_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
