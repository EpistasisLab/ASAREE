import { useEffect, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { skillsApi } from '@/api/client'
import { connectedSkills, skillId, SKILL_FACTOR_PATH } from '@/lib/skillFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useSkillTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const library = useQuery({ queryKey: ['skills'], queryFn: () => skillsApi.list(), enabled: !!graph?.nodes.some((node) => node.data.factor_bindings?.[SKILL_FACTOR_PATH]) })
  const owners = graph?.nodes.filter((node) => node.data.factor_bindings?.[SKILL_FACTOR_PATH] && (!nodeId || node.id === nodeId)) ?? []
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.skill_factor_mode, node.data.skill_selection, connectedSkills(graph!, node.id).map(skillId)]))
  useEffect(() => { setChoices({}) }, [signature])
  const selections = Object.fromEntries(owners.map((node) => {
    if (node.data.skill_factor_mode === 'skill_toggle') return [node.id, choices[node.id] ?? ((node.data.skill_selection as string[] | undefined)?.length ? 'all' : 'none')]
    const ids = connectedSkills(graph!, node.id).map(skillId)
    const baseline = (node.data.skill_selection as string[] | undefined)?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const error = owners.length === 0 ? null : library.isError ? 'Could not load available skills.' : !library.data ? 'Loading skills…' : owners.some((node) => {
    const ids = node.data.skill_factor_mode === 'skill_toggle' ? (selections[node.id] === 'all' ? connectedSkills(graph!, node.id).map(skillId) : []) : [selections[node.id]]
    return ids.some((id) => !library.data.some((skill) => skill.id === id))
  }) ? 'Select an available skill for each agent.' : null
  return { graph, owners, library, selections, options: owners.length ? { skill_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
