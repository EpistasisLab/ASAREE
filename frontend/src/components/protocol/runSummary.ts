import type { Edge, Node } from '@xyflow/react'
import type { DatasetNodeData, LlmNodeData, McpToolNodeData } from '@/types/protocols'

// Plain duplicate of ProtocolCanvas.tsx's own LLM_NODE_TYPES rather than a
// shared import -- same reasoning as nodeConfigIssues.ts's own comment:
// keeps this module from being coupled to that component's internals.
const LLM_NODE_TYPES = new Set(['llm_anthropic', 'llm_openai', 'llm_azure_foundry'])
const DEPENDENCY_HANDLES = new Set(['llm', 'tool', 'memory', 'architectural_pattern'])

export type RunScope = { type: 'graph' } | { type: 'cell'; label: string } | { type: 'node'; nodeId: string; label: string }

export interface RunSummary {
  agentCount: number
  criticGateCount: number
  datasets: string[]
  models: string[]
  toolServers: string[]
}

// Builds a plain-language summary of what a Run click will actually
// execute -- shown in RunConfirmDialog before any real (billable) LLM call
// fires. For scope "graph"/"cell" every node on the canvas participates
// (a cell run is the same graph, just with factor_values substituted); for
// scope "node" (the per-node Play icon) only that node's own directly-wired
// dependencies count, mirroring the one-level connector traversal
// services.protocol_execution's _resolve_llm_config/_resolve_tool_config/
// _resolve_dataset_config do server-side -- kept as a client-side duplicate
// for the same reason nodeConfigIssues.ts already is.
export function summarizeRun(nodes: Node[], edges: Edge[], scope: RunScope): RunSummary {
  const relevantNodes = scope.type === 'node' ? nodesWiredTo(nodes, edges, scope.nodeId) : nodes

  const datasets = uniq(
    relevantNodes
      .filter((n) => n.type === 'dataset')
      .map((n) => (n.data as DatasetNodeData).config?.dataset_name)
      .filter((name): name is string => !!name),
  )
  const models = uniq(
    relevantNodes
      .filter((n) => LLM_NODE_TYPES.has(n.type ?? ''))
      .map((n) => {
        const config = (n.data as LlmNodeData).config
        return config?.model ? `${config.provider}/${config.model}` : null
      })
      .filter((m): m is string => !!m),
  )
  const toolServers = uniq(
    relevantNodes
      .filter((n) => n.type === 'mcp_tool')
      .map((n) => (n.data as McpToolNodeData).config)
      .filter((config) => (config?.enabled ?? true) && (config?.tool_names?.length ?? 0) > 0)
      .map((config) => config.server_name)
      .filter((name): name is string => !!name),
  )

  return {
    agentCount: relevantNodes.filter((n) => n.type === 'agent').length,
    // A per-node run never includes a critic gate (only "agent" nodes ever
    // get the per-node Play icon -- see ProtocolCanvas.tsx's canRunAlone).
    criticGateCount: scope.type === 'node' ? 0 : relevantNodes.filter((n) => n.type === 'critic_gate').length,
    datasets,
    models,
    toolServers,
  }
}

function nodesWiredTo(nodes: Node[], edges: Edge[], targetId: string): Node[] {
  const ids = new Set([targetId, ...edges.filter((e) => e.target === targetId && DEPENDENCY_HANDLES.has(e.targetHandle ?? '')).map((e) => e.source)])
  return nodes.filter((n) => ids.has(n.id))
}

function uniq(values: string[]): string[] {
  return [...new Set(values)]
}
