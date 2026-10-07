import { useEffect, useState } from 'react'
import { connectedScripts, scriptId, SCRIPT_FACTOR_PATH } from '@/lib/scriptFactors'
import type { ProtocolGraph } from '@/types/protocols'

export function useScriptTestSelection(graph: ProtocolGraph | undefined, nodeId?: string) {
  const [choices, setChoices] = useState<Record<string, string>>({})
  const owners = graph?.nodes.filter((node) => node.data.factor_bindings?.[SCRIPT_FACTOR_PATH] && (!nodeId || node.id === nodeId)) ?? []
  const signature = JSON.stringify(owners.map((node) => [node.id, node.data.script_factor_mode, node.data.script_selection, connectedScripts(graph!, node.id).map(scriptId)]))
  useEffect(() => { setChoices({}) }, [signature])
  const selections = Object.fromEntries(owners.map((node) => {
    if (node.data.script_factor_mode === 'script_toggle') return [node.id, choices[node.id] ?? ((node.data.script_selection as string[] | undefined)?.length ? 'all' : 'none')]
    const ids = connectedScripts(graph!, node.id).map(scriptId)
    const baseline = (node.data.script_selection as string[] | undefined)?.[0]
    return [node.id, choices[node.id] ?? (baseline && ids.includes(baseline) ? baseline : ids[0] ?? '')]
  }))
  const error = owners.some((node) => {
    const ids = node.data.script_factor_mode === 'script_toggle' ? selections[node.id] === 'all' ? connectedScripts(graph!, node.id).map(scriptId) : [] : [selections[node.id]]
    return ids.some((id) => !(graph!.nodes.find((node) => node.id === id)?.data.config as { code?: string })?.code?.trim())
  }) ? 'Select a connected Script with code for each agent.' : null
  return { graph, owners, selections, options: owners.length ? { script_selections: selections } : {}, error, setChoice: (agentId: string, id: string) => setChoices((previous) => ({ ...previous, [agentId]: id })) }
}
