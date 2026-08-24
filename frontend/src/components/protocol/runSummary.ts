import type { Edge, Node } from '@xyflow/react'
import type { DatasetNodeData, LlmNodeData, McpToolNodeData, OkfBundleNodeData, SkillNodeData } from '@/types/protocols'

// Plain duplicate of ProtocolCanvas.tsx's own LLM_NODE_TYPES rather than a
// shared import -- same reasoning as nodeConfigIssues.ts's own comment:
// keeps this module from being coupled to that component's internals.
const LLM_NODE_TYPES = new Set(['llm_anthropic', 'llm_openai', 'llm_azure_foundry'])
// Ditto for the MCP-tool family (mcpServerCatalog.ts's MCP_TOOL_NODE_TYPES)
// -- every type in it carries the same config, so a run summary reads the
// server name off any of them identically.
const MCP_TOOL_NODE_TYPES = ['mcp_tool', 'mcp_scikit_learn', 'mcp_client_tool']
// "llm" and "resource" are the pre-rename spellings of "ai" and "dataset"
// (see migrateLegacyHandles in ProtocolCanvas.tsx) -- kept here, as in that
// file's CONNECTOR_HANDLES, so this stays a question of "is this a connector
// edge at all" rather than one that silently answers no for a graph that
// hasn't been normalised yet.
const DEPENDENCY_HANDLES = new Set([
  'ai',
  'llm',
  'tool',
  'memory',
  'architectural_pattern',
  'skill',
  'dataset',
  'resource',
  'knowledge',
])

export type RunScope = { type: 'graph' } | { type: 'cell'; label: string } | { type: 'node'; nodeId: string; label: string }

export interface RunSummary {
  agentCount: number
  criticGateCount: number
  datasets: string[]
  models: string[]
  toolServers: string[]
  skills: string[]
  knowledgeBundles: string[]
}

// Builds a plain-language summary of what a Run click will actually
// execute -- shown in RunConfirmDialog before any real (billable) LLM call
// fires. For scope "graph"/"cell" every node on the canvas participates
// (a cell run is the same graph, just with factor_values substituted); for
// scope "node" (the per-node Play icon) only that node's own directly-wired
// dependencies count, mirroring the one-level connector traversal
// services.protocol_execution's _resolve_llm_config/_resolve_tool_config/
// _resolve_dataset_configs do server-side -- kept as a client-side duplicate
// for the same reason nodeConfigIssues.ts already is.
export function summarizeRun(nodes: Node[], edges: Edge[], scope: RunScope): RunSummary {
  const relevantNodes = scope.type === 'node' ? nodesWiredTo(nodes, edges, scope.nodeId) : nodes

  const datasets = uniq(
    relevantNodes
      .filter((n) => n.type === 'dataset')
      .map((n) => (n.data as DatasetNodeData).config?.dataset_name)
      .filter((name): name is string => !!name),
  )
  // Disabled skill nodes are dropped here, matching _resolve_skill_config's
  // own `enabled is False` skip -- an off skill genuinely doesn't reach the
  // run, so listing it would overstate what's about to happen. (Datasets
  // above don't filter the same way: `enabled` there only skips a prompt
  // block, and the dataset stays attached to the experiment either way.)
  const skills = uniq(
    relevantNodes
      .filter((n) => n.type === 'skill')
      .map((n) => (n.data as SkillNodeData).config)
      .filter((config) => config?.enabled ?? true)
      .map((config) => config?.skill_name)
      .filter((name): name is string => !!name),
  )
  // Same `enabled` filter and same reason as skills: an off bundle's tools
  // never reach the agent's allow-list (_resolve_knowledge_config skips it).
  // Labelled by folder, not by the generated okf-bundle-<hash> server name --
  // the folder is what the user recognises.
  const knowledgeBundles = uniq(
    relevantNodes
      .filter((n) => n.type === 'okf_bundle')
      .map((n) => (n.data as OkfBundleNodeData).config)
      .filter((config) => (config?.enabled ?? true) && !!config?.server_name)
      .map((config) => config.bundle_label ?? config.bundle_path ?? config.server_name)
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
      .filter((n) => MCP_TOOL_NODE_TYPES.includes(n.type ?? ''))
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
    skills,
    knowledgeBundles,
  }
}

function nodesWiredTo(nodes: Node[], edges: Edge[], targetId: string): Node[] {
  const ids = new Set([targetId, ...edges.filter((e) => e.target === targetId && DEPENDENCY_HANDLES.has(e.targetHandle ?? '')).map((e) => e.source)])
  return nodes.filter((n) => ids.has(n.id))
}

function uniq(values: string[]): string[] {
  return [...new Set(values)]
}
