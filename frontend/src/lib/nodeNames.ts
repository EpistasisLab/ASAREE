// What to call a canvas node in prose -- a run panel, a transcript, a warning
// -- when a raw node id ("f3a91c2e-...") tells the reader nothing about which
// box on the canvas it means.
//
// A node's own `data.label` is the answer whenever the user set one, so this is
// really about the ones they didn't: an unlabelled node still renders a
// placeholder on its card ("Dataset", "Output Parser" -- see each node
// component's `placeholder` prop), and that placeholder is what the user is
// looking at, so it's the name they'll recognise. The table below is those
// placeholders; keep it in step with the node components if one is renamed.
const NODE_TYPE_NAMES: Record<string, string> = {
  agent: 'Agent',
  sub_agent: 'Sub-Agent',
  critic_gate: 'Critic Gate',
  dataset: 'Dataset',
  model_anthropic: 'Anthropic',
  model_openai: 'OpenAI',
  model_azure_foundry: 'Azure AI Foundry',
  model_openrouter: 'OpenRouter',
  model_local: 'Local',
  mcp_tool: 'MCP Tool',
  mcp_client_tool: 'MCP Client Tool',
  memory: 'Memory',
  okf_bundle: 'OKF Bundle',
  okf_document: 'OKF Document',
  output_parser: 'Output Parser',
  pattern_reason_act: 'Reason + Act',
  pattern_single_agent_baseline: 'Single-Agent Baseline',
  script: 'Script',
  skill: 'Skill',
}

export interface NamedNode {
  id: string
  type?: string
  data?: { label?: string } | Record<string, unknown>
}

function fallbackName(type: string | undefined): string | null {
  if (!type) return null
  if (NODE_TYPE_NAMES[type]) return NODE_TYPE_NAMES[type]
  // An unknown type is still better read as words than as an id -- a node kind
  // added without a line above shows up as "Web Search", not as a uuid.
  return type.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Friendly names for canvas nodes, by node id.
 *
 * Nodes that end up sharing a name (three unlabelled Scripts) are numbered in
 * canvas order -- "Script 1", "Script 2" -- because a run panel listing three
 * identical rows is no more useful than one listing three ids. A node the
 * caller can't name at all is simply absent from the map, so callers keep their
 * own `?? nodeId` fallback.
 */
export function nodeDisplayNames(nodes: readonly NamedNode[]): Map<string, string> {
  const named = nodes.map((node) => {
    const label = (node.data as { label?: string } | undefined)?.label?.trim()
    return { id: node.id, name: label || fallbackName(node.type) }
  })
  const counts = new Map<string, number>()
  for (const { name } of named) {
    if (name) counts.set(name, (counts.get(name) ?? 0) + 1)
  }
  const seen = new Map<string, number>()
  const result = new Map<string, string>()
  for (const { id, name } of named) {
    if (!name) continue
    if ((counts.get(name) ?? 0) < 2) {
      result.set(id, name)
      continue
    }
    const index = (seen.get(name) ?? 0) + 1
    seen.set(name, index)
    result.set(id, `${name} ${index}`)
  }
  return result
}
