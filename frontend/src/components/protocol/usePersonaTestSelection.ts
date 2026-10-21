import { sharedFactorOwners, sharedGroupIssues } from '@/lib/sharedFactors'
import { useEffect, useState } from 'react'
import { connectedPersonas, personaId, PERSONA_FACTOR_PATH } from '@/lib/personaFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function usePersonaTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const owners = sharedFactorOwners(graph, PERSONA_FACTOR_PATH, nodeId)
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.persona_factor_mode, node.data.persona_selection, connectedPersonas(graph!, node.id).map(personaId)]))
  useEffect(() => { setChoices({}) }, [signature, nodeId])
  const selections = Object.fromEntries(owners.map((node) => {
    if (node.data.persona_factor_mode === 'persona_toggle') return [node.id, choices[node.id] ?? ((node.data.persona_selection as string[] | undefined)?.length ? 'all' : 'none')]
    const ids = connectedPersonas(graph!, node.id).map(personaId)
    const baseline = (node.data.persona_selection as string[] | undefined)?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const selectionError = owners.some((node) => {
    const ids = node.data.persona_factor_mode === 'persona_toggle' ? selections[node.id] === 'all' ? connectedPersonas(graph!, node.id).map(personaId) : [] : [selections[node.id]]
    return ids.some((id) => !connectedPersonas(graph!, node.id).some((persona) => persona.id === id))
  }) ? 'Select a connected Persona for each shared group.' : null
  const factors = owners.map((node) => ({ name: node.data.factor_bindings![PERSONA_FACTOR_PATH], level_type: (node.data.persona_factor_mode ?? 'persona_selection') as 'persona_selection' | 'persona_toggle', levels: [] }))
  const error = sharedGroupIssues(graph, factors, 'persona')[0] ?? selectionError
  return { graph, owners, selections, options: owners.length ? { persona_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
