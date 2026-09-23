import type { CoordinationStrategySlug } from '@/types/experiments'
import type { AgentNodeData, ProtocolEdge, ProtocolGraph, ProtocolNode } from '@/types/protocols'

// Mirrors services.protocol_execution's own _CONNECTOR_HANDLES -- any edge
// whose targetHandle ISN'T one of these is a plain "main" pipeline edge.
// Includes the pre-rename "ai", "llm", and "resource" spellings for the same reason
// the backend set does: a graph that hasn't been through migrateLegacyHandles
// yet must not have its Model/Dataset edges misread as main pipeline edges.
export const CONNECTOR_HANDLES = new Set([
  'model',
  'ai',
  'llm',
  'tool',
  'memory',
  'architectural_pattern',
  'skill',
  'dataset',
  'resource',
  'knowledge',
  'output_parser',
])

export function isMainEdge(edge: Pick<ProtocolEdge, 'targetHandle'>): boolean {
  return !CONNECTOR_HANDLES.has(edge.targetHandle ?? '')
}

function nodeName(node: ProtocolNode): string {
  const label = (node.data as { label?: string } | undefined)?.label
  return label && label.trim() ? label : node.type === 'agent' ? 'Agent' : node.type
}

/** Agent-to-agent handoffs, following main edges *through* non-agent nodes.
 *
 * Mirrors `_sequential_agent_links` on the backend: a handoff is a path, not
 * necessarily one edge, because a Critic Gate or a Script sitting between two
 * agents is plumbing rather than a link in the chain. */
function agentLinks(graph: ProtocolGraph) {
  const nodes = new Map(graph.nodes.map((n) => [n.id, n]))
  const agents = graph.nodes.filter((n) => n.type === 'agent').map((n) => n.id)
  const downstream = new Map<string, string[]>()
  for (const edge of graph.edges) {
    if (!isMainEdge(edge)) continue
    if (!nodes.has(edge.source) || !nodes.has(edge.target)) continue
    downstream.set(edge.source, [...(downstream.get(edge.source) ?? []), edge.target])
  }
  const successors = new Map<string, string[]>(agents.map((id) => [id, []]))
  const predecessors = new Map<string, string[]>(agents.map((id) => [id, []]))
  for (const agentId of agents) {
    const frontier = [...(downstream.get(agentId) ?? [])]
    const seen = new Set([agentId])
    while (frontier.length) {
      const current = frontier.shift()!
      if (seen.has(current)) continue
      seen.add(current)
      if (nodes.get(current)?.type === 'agent') {
        if (!successors.get(agentId)!.includes(current)) {
          successors.get(agentId)!.push(current)
          predecessors.get(current)!.push(agentId)
        }
        continue
      }
      frontier.push(...(downstream.get(current) ?? []))
    }
  }
  return { agents, successors, predecessors, nodes }
}

/** Why the canvas can't run under `slug`, as user-facing sentences.
 *
 * A design-time mirror of `validate_coordination_strategy`, so picking an
 * incompatible strategy says so under the picker instead of being rejected at
 * publish or run time. Deliberately advisory: the strategy has to be selectable
 * *before* the canvas matches it, or the design loop deadlocks -- you couldn't
 * pick Peer Collaboration while the canvas is a chain, and couldn't wire a peer
 * loop while Sequential forbids forks.
 *
 * The backend remains the authority. Anything this misses is still caught
 * there; the point here is to catch the common cases early, not to be the gate.
 */
export function coordinationStrategyIssues(slug: CoordinationStrategySlug, graph: ProtocolGraph | undefined): string[] {
  if (!graph) return []
  const { agents, successors, predecessors, nodes } = agentLinks(graph)
  const name = (id: string) => nodeName(nodes.get(id)!)

  if (slug === 'critic_gate') {
    return graph.nodes.some((n) => n.type === 'critic_gate')
      ? []
      : ['This protocol has no Critic Gate node wired in.']
  }

  if (slug === 'peer_collaboration') {
    const connected = agents.filter((id) => successors.get(id)!.length || predecessors.get(id)!.length)
    if (connected.length === 0) {
      return ['No two Agent nodes on this protocol are connected, so nobody has anyone to talk to.']
    }
    const marked = connected.filter((id) => (nodes.get(id)!.data as AgentNodeData).conversation_lead === true)
    if (marked.length > 1) {
      return [`More than one agent is marked as the conversation lead (${marked.map(name).sort().join(', ')}).`]
    }
    if (marked.length === 1) return []
    const unfed = connected.filter((id) => predecessors.get(id)!.length === 0)
    if (unfed.length === 0) {
      return [
        'Every connected agent is fed by another, so there is no obvious agent to start the conversation. Mark one as the conversation lead.',
      ]
    }
    if (unfed.length > 1) {
      return [
        `More than one agent could start the conversation (${unfed.map(name).sort().join(', ')}). Mark one as the conversation lead.`,
      ]
    }
    return []
  }

  if (slug === 'supervisor_architecture') {
    // Mirrors resolve_supervisor_roles. Same order, same first-failure-wins
    // shape, so the canvas and the publish button never disagree about which
    // problem to name first.
    if (agents.length < 2) return ['This protocol has fewer than two Agent nodes, so there is nobody to supervise.']
    const marked = agents.filter((id) => (nodes.get(id)!.data as AgentNodeData).conversation_lead === true)
    if (marked.length > 1) {
      return [`More than one agent is marked as the supervisor (${marked.map(name).sort().join(', ')}).`]
    }
    let supervisor: string
    if (marked.length === 1) {
      supervisor = marked[0]
    } else {
      // Mirrors _supervisor_candidates: the agent nothing feeds into, or -- in
      // a loop, which the target topology is once the reviewer reports back --
      // the one dispatching to the most others.
      const unfed = agents.filter((id) => successors.get(id)!.length && predecessors.get(id)!.length === 0)
      const widest = Math.max(0, ...agents.map((id) => successors.get(id)!.length))
      const candidates = unfed.length
        ? unfed
        : widest
          ? agents.filter((id) => successors.get(id)!.length === widest)
          : []
      if (candidates.length !== 1) {
        return [
          candidates.length
            ? `More than one agent could be the supervisor (${candidates.map(name).sort().join(', ')}). Mark one in its node settings.`
            : 'No agent hands off to another, so none of them leads. Mark the supervisor in its node settings.',
        ]
      }
      supervisor = candidates[0]
    }
    const workers = successors.get(supervisor)!
    if (!workers.length) return [`${name(supervisor)} hands off to no other agent, so there are no workers.`]
    const rest = agents.filter((id) => id !== supervisor && !workers.includes(id))
    if (rest.length > 1) {
      return [
        `${rest.map(name).sort().join(', ')} are neither the supervisor nor agents it hands off to. A supervisor run has one supervisor, its workers, and at most one reviewer.`,
      ]
    }
    // Undirected adjacency, matching _connected_agent_ids: how the user drew an
    // edge says who dispatches, but "is connected to" ignores its direction.
    const neighbours = (id: string) => [...successors.get(id)!, ...predecessors.get(id)!]
    if (rest.length === 1 && new Set(neighbours(rest[0])).size < 2) {
      return [`${name(rest[0])} reviews this run but is connected to only one other agent.`]
    }
    for (const id of workers) {
      const peers = neighbours(id).filter((other) => workers.includes(other))
      if (peers.length) {
        return [`${name(id)} is wired to another worker (${peers.map(name).sort().join(', ')}), which is a peer mesh.`]
      }
    }
    return []
  }

  // sequential -- the chain rule, mirroring validate_sequential_chain.
  if (agents.length < 2) return []
  const issues: string[] = []
  for (const id of agents) {
    if (successors.get(id)!.length > 1) {
      issues.push(`${name(id)} hands off to more than one agent (${successors.get(id)!.map(name).sort().join(', ')}).`)
    }
    if (predecessors.get(id)!.length > 1) {
      issues.push(
        `More than one agent hands off to ${name(id)} (${predecessors.get(id)!.map(name).sort().join(', ')}).`,
      )
    }
  }
  if (issues.length) return issues
  const heads = agents.filter((id) => predecessors.get(id)!.length === 0)
  if (heads.length === 0) return ['Every agent is fed by another agent, so a sequential run has nowhere to start.']
  if (heads.length > 1) {
    return [`This protocol has ${heads.length} separate agent chains, starting at ${heads.map(name).sort().join(', ')}.`]
  }
  const reachable = new Set<string>()
  let cursor: string | undefined = heads[0]
  while (cursor && !reachable.has(cursor)) {
    reachable.add(cursor)
    cursor = successors.get(cursor)![0]
  }
  const stranded = agents.filter((id) => !reachable.has(id))
  if (stranded.length) {
    return [`${stranded.map(name).sort().join(', ')} cannot be reached from the start of the chain.`]
  }
  return []
}
