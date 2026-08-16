import type { Edge, Node } from '@xyflow/react'
import type { DatasetNodeData, LlmNodeData, McpToolNodeData, ScriptNodeData } from '@/types/protocols'

export interface NodeConfigIssue {
  nodeId: string
  label: string
  issues: string[]
}

// A pre-flight scan run right before a real Run fires, so an obviously
// misconfigured node (no model, no dataset picked, no script code, an
// agent with nothing wired into its required LLM connector) surfaces as an
// upfront "run anyway?" confirmation instead of only ever showing up as a
// generic "one or more nodes failed" AFTER a real (billable) run attempt.
// Mirrors the SAME cheap, synchronous presence checks each node's own
// canvas card already computes for its own warning triangle (LlmNode.tsx/
// McpToolNode.tsx/DatasetNode.tsx/ScriptNode.tsx) -- kept as a plain
// duplicate rather than a shared import specifically because LlmNode's own
// check is richer (it also validates the model against a live, query-
// backed discovered list); this scan deliberately skips that part so
// clicking Run never has to wait on a network round-trip first. A model
// that's merely unset is still caught here, just not one that's set but
// stale -- update both places together if these conditions ever change.
export function findNodeConfigIssues(nodes: Node[], edges: Edge[]): NodeConfigIssue[] {
  const agentIdsWithLlm = new Set(edges.filter((e) => e.targetHandle === 'llm').map((e) => e.target))
  const result: NodeConfigIssue[] = []

  for (const node of nodes) {
    const data = node.data as { label?: string } | undefined
    const label = data?.label || node.type || 'node'
    const issues: string[] = []

    switch (node.type) {
      case 'agent':
        if (!agentIdsWithLlm.has(node.id)) issues.push('No LLM connected')
        break
      case 'llm_anthropic':
      case 'llm_openai':
      case 'llm_azure_foundry': {
        const config = (node.data as LlmNodeData).config
        if (!config?.model) issues.push('No model set')
        break
      }
      case 'mcp_tool': {
        const config = (node.data as McpToolNodeData).config
        if (!((config?.tool_names?.length ?? 0) > 0)) issues.push('Not configured -- pick a server and at least one tool')
        break
      }
      case 'dataset': {
        const config = (node.data as DatasetNodeData).config
        if (!config?.dataset_id) issues.push('No dataset selected')
        break
      }
      case 'script': {
        const config = (node.data as ScriptNodeData).config
        if (!config?.code) issues.push('No code set')
        break
      }
      default:
        break
    }

    if (issues.length > 0) result.push({ nodeId: node.id, label, issues })
  }

  return result
}
